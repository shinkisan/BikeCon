#!/bin/bash
set -e

# BikeCon Installation Script
# This script installs BikeCon to the system following FHS standard

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "BikeCon Installation Script"
echo "=========================================="
echo ""

# Step 0: Check if running as root
echo "[Step 0] Checking root privileges..."
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}ERROR: This script must be run as root${NC}"
   echo "Use: sudo ./install.sh"
   exit 1
fi
echo -e "${GREEN}✓ Running as root${NC}"
echo ""

# Step 0.1: Check identity.json early
echo "[Step 0.1] Checking identity configuration..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$SCRIPT_DIR/identity.json" ]]; then
    echo -e "${RED}ERROR: identity.json not found!${NC}"
    echo ""
    echo -e "  ${RED}CRITICAL: identity.json must be generated BEFORE running this installation script.${NC}"
    echo ""
    echo "  How to generate identity.json:"
    echo "  1. Capture BLE HCI traffic from your Keep app:"
    echo "     - On your Android device running Keep app"
    echo "     - Enable HCI logging in Developer Options:"
    echo "       Settings → Developer Options → Enable Bluetooth HCI snoop log"
    echo "     - Run Keep app and connect to your bike"
    echo "     - Disable HCI logging when done"
    echo "     - Extract HCI log file (usually /data/misc/bluetooth/logs/btsnoop_hci.log before Android 11"
    echo "       or run 'adb bugreport bugreport.zip' and extract the zip file to find the log at"
    echo "       FS/data/misc/bluetooth/logs/btsnoop_hci.log)"
    echo ""
    echo "  2. Run identity_gen.py to extract credentials:"
    echo "     python3 identity_gen.py /data/misc/bluetooth/logs/btsnoop_hci.log"
    echo ""
    echo "  3. This will generate identity.json containing:"
    echo "     - bike_name: Your bike model"
    echo "     - bike_mac: Bike Bluetooth MAC address"
    echo "     - phone_mac: Your phone Bluetooth MAC address"
    echo "     - uuid1, uuid2: Keep app authentication UUIDs"
    echo ""
    echo "  4. Once identity.json is created, run this installation script again:"
    echo "     sudo ./install.sh"
    echo ""
    echo "Installation aborted."
    exit 1
fi
echo -e "${GREEN}✓ identity.json found${NC}"
echo ""

# Step 1: Check USB Gadget configuration
echo "[Step 1] Checking USB Gadget configuration..."
if grep -q "dtoverlay=dwc2" /boot/firmware/config.txt; then
    if grep -q "dtoverlay=dwc2,dr_mode=peripheral" /boot/firmware/config.txt; then
        echo -e "${GREEN}✓ USB Gadget is properly configured (dr_mode=peripheral)${NC}"
    else
        echo -e "${YELLOW}⚠ USB Gadget is configured but dr_mode might not be set to peripheral${NC}"
        echo "  Current config:"
        grep "dtoverlay=dwc2" /boot/firmware/config.txt
        echo ""
        echo "  To fix this, edit /boot/firmware/config.txt and ensure:"
        echo "    dtoverlay=dwc2,dr_mode=peripheral"
        echo ""
        echo "  This is required for HID gadget functionality."
        echo "  Continuing without verification - you may need to reboot after editing."
    fi
else
    echo -e "${YELLOW}⚠ USB Gadget not configured in /boot/firmware/config.txt${NC}"
    echo ""
    echo "  To enable USB HID Gadget mode, add this line to /boot/firmware/config.txt:"
    echo "    dtoverlay=dwc2,dr_mode=peripheral"
    echo ""
    echo "  Below [all] section. Then reboot."
    echo "  This is REQUIRED for HID gadget functionality (joy-con input)."
    echo ""
    read -p "  Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Installation cancelled."
        exit 1
    fi
fi
echo ""

# Step 2: Create directory structure
echo "[Step 2] Creating directory structure..."
mkdir -p /opt/BikeCon
mkdir -p /etc/BikeCon
mkdir -p /var/log/BikeCon
mkdir -p /var/run/BikeCon
echo -e "${GREEN}✓ Directories created${NC}"
echo ""

# Step 2.1: Copy usb_gamepad.sh
echo "[Step 2.1] Installing usb_gamepad.sh..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/usb_gamepad.sh" ]]; then
    cp "$SCRIPT_DIR/usb_gamepad.sh" /usr/local/bin/usb_gamepad.sh
    chmod +x /usr/local/bin/usb_gamepad.sh
    echo -e "${GREEN}✓ usb_gamepad.sh copied to /usr/local/bin/${NC}"
else
    echo -e "${RED}ERROR: usb_gamepad.sh not found in $SCRIPT_DIR${NC}"
    exit 1
fi
echo ""

# Step 3: Copy application code
echo "[Step 3] Copying application code to /opt/BikeCon..."
for pyfile in "$SCRIPT_DIR"/*.py; do
    filename=$(basename "$pyfile")
    # Skip identity_gen.py as it's only for credential extraction, not deployment
    if [[ "$filename" != "identity_gen.py" ]]; then
        cp "$pyfile" /opt/BikeCon/
    fi
done
cp "$SCRIPT_DIR/index.html" /opt/BikeCon/ 2>/dev/null || true
echo -e "${GREEN}✓ Application code copied${NC}"
echo ""

# Step 4: Create virtual environment and install dependencies
echo "[Step 4] Setting up Python virtual environment..."
cd /opt/BikeCon

# Check if venv already exists
if [[ ! -d "venv" ]]; then
    echo "  Creating new virtual environment..."
    python3 -m venv venv
fi

# Activate venv and install dependencies
source venv/bin/activate
echo "  Installing dependencies from requirements.txt..."
if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
    pip install --upgrade pip
    pip install -r "$SCRIPT_DIR/requirements.txt"
    echo -e "${GREEN}✓ Dependencies installed${NC}"
else
    echo -e "${YELLOW}⚠ requirements.txt not found, skipping pip install${NC}"
    echo "  Please manually install dependencies:"
    echo "    source /opt/BikeCon/BikeCon/bin/activate"
    echo "    pip install -r requirements.txt"
fi
deactivate
echo ""

# Step 5: Copy config.json
echo "[Step 5] Installing configuration template..."
if [[ -f "$SCRIPT_DIR/config.json" ]]; then
    cp "$SCRIPT_DIR/config.json" /etc/BikeCon/config.json
    echo -e "${GREEN}✓ config.json copied to /etc/BikeCon/${NC}"
else
    echo -e "${RED}WARNING: config.json not found${NC}"
fi
echo ""

# Step 5.1: Install identity.json
echo "[Step 5.1] Installing identity configuration..."
cp "$SCRIPT_DIR/identity.json" /etc/BikeCon/identity.json
chmod 755 /etc/BikeCon/identity.json
echo -e "${GREEN}✓ identity.json copied to /etc/BikeCon/${NC}"
echo ""

# Step 6: Set file permissions
echo "[Step 6] Setting file permissions..."
chown -R root:root /opt/BikeCon
chmod 755 /opt/BikeCon
chmod 755 /opt/BikeCon/*.py 2>/dev/null || true

chown -R root:root /etc/BikeCon
chmod 755 /etc/BikeCon

chown -R root:root /var/log/BikeCon
chmod 755 /var/log/BikeCon

chown -R root:root /var/run/BikeCon
chmod 755 /var/run/BikeCon

chmod +x /usr/local/bin/usb_gamepad.sh
echo -e "${GREEN}✓ Permissions set${NC}"
echo ""

# Step 7: Install systemd service files
echo "[Step 7] Installing systemd service files..."
if [[ -d "$SCRIPT_DIR/systemd" ]]; then
    if cp "$SCRIPT_DIR/systemd"/BikeCon-*.service /etc/systemd/system/; then
        echo -e "${GREEN}✓ Service files copied${NC}"
    else
        echo -e "${RED}ERROR: Copy failed. Check if files exist or run with sudo.${NC}"
        exit 1 
    fi
else
    echo -e "${RED}WARNING: systemd directory not found at $SCRIPT_DIR/systemd${NC}"
fi

# Step 8: Reload systemd daemon
echo "[Step 8] Reloading systemd daemon..."
systemctl daemon-reload
systemctl enable BikeCon-hardware.service 2>/dev/null || true
systemctl enable BikeCon-mixer.service 2>/dev/null || true
systemctl enable BikeCon-bike.service 2>/dev/null || true
systemctl enable BikeCon-joycon.service 2>/dev/null || true
systemctl enable BikeCon-web.service 2>/dev/null || true
echo -e "${GREEN}✓ Systemd services registered and enabled${NC}"
echo ""

echo "=========================================="
echo -e "${GREEN}Installation complete!${NC}"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Verify identity.json configuration:"
echo "   cat /etc/BikeCon/identity.json"
echo "2. Start the services:"
echo "   sudo ./start.sh or start individual services with systemctl:"
echo "   sudo systemctl start BikeCon-hardware.service"
echo "   sudo systemctl start BikeCon-mixer.service"
echo "   sudo systemctl start BikeCon-bike.service"
echo "   sudo systemctl start BikeCon-web.service"
echo "   sudo systemctl start BikeCon-joycon.service"
echo ""
echo "4. View logs:"
echo "   journalctl -u BikeCon-bike.service -f"
echo "   Bike Raw Data Logs: tail -f /dev/shm/BikeCon/bike_raw_data.log"
echo ""
echo "System information:"
echo "  Install directory: /opt/BikeCon"
echo "  Config directory:  /etc/BikeCon"
echo "  Log directory:     /var/log/BikeCon"
echo "  Runtime directory: /var/run/BikeCon"
echo ""
