#!/bin/bash
set -e

# BikeCon Uninstallation Script
# This script removes BikeCon from the system

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=========================================="
echo "BikeCon Uninstallation Script"
echo "=========================================="
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}ERROR: This script must be run as root${NC}"
   echo "Use: sudo ./uninstall.sh"
   exit 1
fi

# Confirm uninstallation
echo -e "${YELLOW}WARNING: This will remove BikeCon from your system${NC}"
echo ""
echo "The following will be removed:"
echo "  - /opt/BikeCon (application code and venv)"
echo "  - /etc/systemd/system/BikeCon-*.service"
echo "  - /etc/tmpfiles.d/bikecon.conf"
echo "  - /usr/local/bin/usb_gamepad.sh"
echo ""
echo "The following will be KEPT (in case you need to recover data):"
echo "  - /etc/BikeCon/config.json (your application settings)"
echo "  - /etc/BikeCon/identity.json (your sensitive device credentials)"
echo "  - /var/lib/BikeCon/ (session history database)"
echo "  - /var/log/BikeCon/ (logs)"
echo ""
read -p "Are you sure you want to uninstall BikeCon? (yes/no) " -r
echo
if [[ ! $REPLY == "yes" ]]; then
    echo "Uninstallation cancelled."
    exit 0
fi

echo ""
echo "=========================================="
echo "Starting uninstallation..."
echo "=========================================="
echo ""

# Step 1: Stop and disable services
echo "[Step 1] Stopping and disabling services..."
systemctl stop BikeCon-bike.service 2>/dev/null || true
systemctl stop BikeCon-joycon.service 2>/dev/null || true
systemctl stop BikeCon-web.service 2>/dev/null || true
systemctl stop BikeCon-mixer.service 2>/dev/null || true
systemctl stop BikeCon-ftms.service 2>/dev/null || true
echo "  Services stopped."

systemctl disable BikeCon-bike.service 2>/dev/null || true
systemctl disable BikeCon-joycon.service 2>/dev/null || true
systemctl disable BikeCon-web.service 2>/dev/null || true
systemctl disable BikeCon-mixer.service 2>/dev/null || true
systemctl disable BikeCon-ftms.service 2>/dev/null || true
echo "  Services disabled."
echo ""

# Step 2: Remove service files
echo "[Step 2] Removing systemd service files..."
rm -f /etc/systemd/system/BikeCon-*.service
if [[ -f /etc/tmpfiles.d/bikecon.conf ]]; then
    systemd-tmpfiles --remove /etc/tmpfiles.d/bikecon.conf 2>/dev/null || true
    rm -f /etc/tmpfiles.d/bikecon.conf
fi
systemctl daemon-reload
echo -e "${GREEN}✓ Service files removed${NC}"
echo ""

# Step 3: Remove application directory
echo "[Step 3] Removing application directory..."
if [[ -d "/opt/BikeCon" ]]; then
    rm -rf /opt/BikeCon
    echo -e "${GREEN}✓ /opt/BikeCon removed${NC}"
else
    echo "  /opt/BikeCon not found, skipping."
fi
echo ""

# Step 4: Remove usb_gamepad.sh
echo "[Step 4] Removing usb_gamepad.sh..."
if [[ -f "/usr/local/bin/usb_gamepad.sh" ]]; then
    rm -f /usr/local/bin/usb_gamepad.sh
    echo -e "${GREEN}✓ /usr/local/bin/usb_gamepad.sh removed${NC}"
else
    echo "  File not found, skipping."
fi
echo ""

# Step 5: Preserve configuration and logs
echo "[Step 5] Preserving user data..."
echo -e "  ${GREEN}✓ /etc/BikeCon/config.json preserved${NC}"
echo -e "  ${GREEN}✓ /etc/BikeCon/identity.json preserved (sensitive credentials)${NC}"
echo -e "  ${GREEN}✓ /var/lib/BikeCon/ preserved (session history)${NC}"
echo -e "  ${GREEN}✓ /var/log/BikeCon/ preserved${NC}"
echo ""

echo "=========================================="
echo -e "${GREEN}Uninstallation complete!${NC}"
echo "=========================================="
echo ""
echo "Remaining directories:"
echo "  /etc/BikeCon/ - Configuration (preserved)"
echo "    - config.json: Application settings"
echo "    - identity.json: Device credentials (keep secure!)"
echo "  /var/lib/BikeCon/ - Session history (preserved)"
echo "  /var/log/BikeCon/ - Logs (preserved)"
echo "  /var/run/BikeCon/ - Runtime files"
echo ""
echo "To remove configuration and logs as well, run:"
echo "  sudo rm -rf /etc/BikeCon /var/lib/BikeCon /var/log/BikeCon /var/run/BikeCon"
