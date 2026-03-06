#!/bin/bash
set -e

# BikeCon Service Start Script
# This script starts all BikeCon services in the correct order

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "=========================================="
echo "BikeCon Service Start Script"
echo "=========================================="
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}ERROR: This script must be run as root${NC}"
   echo "Use: sudo ./start.sh"
   exit 1
fi
echo -e "${GREEN}✓ Running as root${NC}"
echo ""

# Function to check service status
check_service() {
    local service_name=$1
    local max_attempts=10
    local attempt=1

    echo -n "  Waiting for $service_name to start..."
    while [[ $attempt -le $max_attempts ]]; do
        if systemctl is-active --quiet "$service_name"; then
            echo -e " ${GREEN}✓${NC}"
            return 0
        fi
        echo -n "."
        sleep 1
        ((attempt++))
    done

    echo -e " ${RED}✗${NC}"
    echo -e "${YELLOW}WARNING: $service_name failed to start within 10 seconds${NC}"
    return 1
}

# Function to start service
start_service() {
    local service_name=$1
    local description=$2

    echo -e "${BLUE}[Starting]${NC} $description..."
    if systemctl start "$service_name" 2>/dev/null; then
        check_service "$service_name"
    else
        echo -e "${RED}ERROR: Failed to start $service_name${NC}"
        return 1
    fi
}

# Step 1: Start hardware service (USB gadget configuration)
echo "Step 1: Starting hardware service..."
start_service "BikeCon-hardware.service" "USB Hardware Configuration"

# Step 2: Start mixer service (gamepad emulation)
echo ""
echo "Step 2: Starting mixer service..."
start_service "BikeCon-mixer.service" "Gamepad Mixer Service"

# Step 3: Start bike service (BLE connection and data processing)
echo ""
echo "Step 3: Starting bike service..."
start_service "BikeCon-bike.service" "BLE Bike Connection Service"

# Step 4: Start joycon service (gamepad input handling)
echo ""
echo "Step 4: Starting joycon service..."
start_service "BikeCon-joycon.service" "Joy-Con Input Service"

# Step 5: Start web service (web interface)
echo ""
echo "Step 5: Starting web service..."
start_service "BikeCon-web.service" "Web Interface Service"

echo ""
echo "=========================================="
echo -e "${GREEN}Service startup complete!${NC}"
echo "=========================================="
echo ""

# Show service status
echo "Service Status:"
echo "---------------"
systemctl status BikeCon-hardware.service --no-pager -l | grep -E "(Active|Loaded|Status)"
echo ""
systemctl status BikeCon-mixer.service --no-pager -l | grep -E "(Active|Loaded|Status)"
echo ""
systemctl status BikeCon-bike.service --no-pager -l | grep -E "(Active|Loaded|Status)"
echo ""
systemctl status BikeCon-joycon.service --no-pager -l | grep -E "(Active|Loaded|Status)"
echo ""
systemctl status BikeCon-web.service --no-pager -l | grep -E "(Active|Loaded|Status)"

echo ""
echo "=========================================="
echo "Access Information:"
echo "=========================================="
echo "Web Interface: http://localhost:8000"
echo "Bike Service Logs: journalctl -u BikeCon-bike.service -f"
echo "All Logs: journalctl -u BikeCon-*.service -f"
echo ""
echo "To stop all services: sudo ./stop.sh"
echo ""

# Check if web service is listening
if systemctl is-active --quiet "BikeCon-web.service"; then
    echo -e "${GREEN}✓ All services started successfully!${NC}"
else
    echo -e "${YELLOW}⚠ Some services may have issues. Check logs above.${NC}"
fi