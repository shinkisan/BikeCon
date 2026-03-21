#!/bin/bash
set -e

# BikeCon Service Stop Script
# This script stops all BikeCon services in reverse order

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "=========================================="
echo "BikeCon Service Stop Script"
echo "=========================================="
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}ERROR: This script must be run as root${NC}"
   echo "Use: sudo ./stop.sh"
   exit 1
fi
echo -e "${GREEN}✓ Running as root${NC}"
echo ""

# Function to stop service
stop_service() {
    local service_name=$1
    local description=$2

    echo -e "${BLUE}[Stopping]${NC} $description..."
    if systemctl stop "$service_name" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $service_name stopped"
    else
        echo -e "  ${YELLOW}⚠${NC} $service_name may not be running"
    fi
}

# Stop services in reverse order to avoid dependency issues

# Step 1: Stop web service first (web interface)
echo "Step 1: Stopping web service..."
stop_service "BikeCon-web.service" "Web Interface Service"

# Step 2: Stop joycon service
echo ""
echo "Step 2: Stopping joycon service..."
stop_service "BikeCon-joycon.service" "Joy-Con Input Service"

# Step 3: Stop bike service
echo ""
echo "Step 3: Stopping bike service..."
stop_service "BikeCon-bike.service" "BLE Bike Connection Service"

# Step 4: Stop FTMS service
echo ""
echo "Step 4: Stopping FTMS service..."
stop_service "BikeCon-ftms.service" "FTMS BLE Server Service"

# Step 5: Stop mixer service
echo ""
echo "Step 5: Stopping mixer service..."
stop_service "BikeCon-mixer.service" "Gamepad Mixer Service"

echo ""
echo "=========================================="
echo -e "${GREEN}All services stopped!${NC}"
echo "=========================================="
echo ""

# Show final service status
echo "Final Service Status:"
echo "--------------------"
systemctl status BikeCon-mixer.service --no-pager -l | grep -E "(Active|Loaded)" || echo "BikeCon-mixer.service: not loaded"
echo ""
systemctl status BikeCon-ftms.service --no-pager -l | grep -E "(Active|Loaded)" || echo "BikeCon-ftms.service: not loaded"
echo ""
systemctl status BikeCon-bike.service --no-pager -l | grep -E "(Active|Loaded)" || echo "BikeCon-bike.service: not loaded"
echo ""
systemctl status BikeCon-joycon.service --no-pager -l | grep -E "(Active|Loaded)" || echo "BikeCon-joycon.service: not loaded"
echo ""
systemctl status BikeCon-web.service --no-pager -l | grep -E "(Active|Loaded)" || echo "BikeCon-web.service: not loaded"

echo ""
echo "=========================================="
echo "Cleanup Information:"
echo "=========================================="
echo "All BikeCon services have been stopped."
echo "To restart: sudo ./start.sh"
echo "To check logs: journalctl -u BikeCon-*.service -f"
echo ""

# Optional: Clean up any remaining processes
echo "Checking for any remaining BikeCon processes..."
remaining_processes=$(pgrep -f "bikecon\|BikeCon" || true)
if [[ -n "$remaining_processes" ]]; then
    echo -e "${YELLOW}Found remaining processes, killing them...${NC}"
    pkill -f "bikecon\|BikeCon" || true
    sleep 2
    echo -e "${GREEN}✓ Cleanup complete${NC}"
else
    echo -e "${GREEN}✓ No remaining processes found${NC}"
fi