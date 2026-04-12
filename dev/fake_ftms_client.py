#!/usr/bin/env python3
"""
FTMS Client Test with Web Interface

Usage:
    python ftms_test.py [device_name_or_mac]
    
Examples:
    python ftms_test.py                    # Scan for BikeCon-FTMS
    python ftms_test.py AA:BB:CC:DD:EE:FF  # Connect by MAC address
    python ftms_test.py BikeCon-FTMS       # Connect by device name
    python ftms_test.py -i                 # Interactive console mode
    python ftms_test.py --no-web           # Without web interface
"""

import asyncio
import sys
import os
import argparse
import logging
import logging.handlers
from datetime import datetime
from typing import Optional

# Create logs directory
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Configure logging
LOG_FILE = os.path.join(LOG_DIR, f"ftms_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Create logger
logger = logging.getLogger("FTMS")
logger.setLevel(logging.DEBUG)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
console_handler.setFormatter(console_formatter)

# File handler
file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=10*1024*1024, backupCount=3
)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
file_handler.setFormatter(file_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

from aiohttp import web
from bleak import BleakClient, BleakScanner
from pycycling.fitness_machine_service import FitnessMachineService
from pycycling.ftms_parsers import IndoorBikeData


class FTMSClient:
    def __init__(self, address: str):
        self.address = address
        self.client: Optional[BleakClient] = None
        self.ftms: Optional[FitnessMachineService] = None
        self.running = False
        
        self.current_data = {
            "cadence": 0,
            "power": 0,
            "speed": 0.0,
            "distance": 0,
            "resistance": 0,
            "calories": 0,
            "status": "disconnected"
        }
    
    async def connect(self):
        logger.info(f"Connecting to {self.address}...")
        self.client = BleakClient(self.address, timeout=10)
        
        try:
            await self.client.connect()
            logger.info("BLE connection established")
        except Exception as e:
            logger.error(f"BLE connection failed: {e}")
            raise
        
        # Get services
        logger.info("Fetching services...")
        try:
            services = await self.client.get_services()
            logger.info(f"Found {len(services.services)} services:")
            for svc in services.services.values():
                logger.info(f"  - {svc.uuid}")
                for char in svc.characteristics:
                    logger.info(f"      {char.uuid}")
        except Exception as e:
            logger.error(f"Failed to get services: {e}")
        
        self.ftms = FitnessMachineService(self.client)
        
        self.ftms.set_indoor_bike_data_handler(self._on_indoor_bike_data)
        self.ftms.set_control_point_response_handler(self._on_control_response)
        self.ftms.set_fitness_machine_status_handler(self._on_status)
        
        try:
            await self.ftms.enable_indoor_bike_data_notify()
            logger.info("Indoor bike data notifications enabled")
        except Exception as e:
            logger.error(f"Failed to enable indoor bike data notify: {e}")
            raise
        
        try:
            await self.ftms.enable_control_point_indicate()
            logger.info("Control point indicate enabled")
        except Exception as e:
            logger.warning(f"Failed to enable control point indicate: {e}")
        
        try:
            await self.ftms.request_control()
            logger.info("Control requested")
        except Exception as e:
            logger.warning(f"Failed to request control: {e}")
        
        self.running = True
        logger.info(f"Connected to {self.address}")
    
    async def disconnect(self):
        self.running = False
        if self.client:
            await self.client.disconnect()
        logger.info("Disconnected")
    
    def _on_indoor_bike_data(self, data: IndoorBikeData):
        self.current_data["cadence"] = data.instant_cadence or 0
        self.current_data["power"] = data.instant_power or 0
        self.current_data["speed"] = data.instant_speed or 0.0
        self.current_data["distance"] = data.total_distance or 0
        self.current_data["resistance"] = data.resistance_level or 0
        self.current_data["calories"] = data.total_energy or 0
        self.current_data["status"] = "connected"
        
        logger.info(f"Data: Cadence: {self.current_data['cadence']} RPM | "
              f"Power: {self.current_data['power']} W | "
              f"Speed: {self.current_data['speed']:.1f} km/h | "
              f"Distance: {self.current_data['distance']} m")
    
    def _on_control_response(self, response):
        logger.info(f"Control response: {response}")
    
    def _on_status(self, status):
        logger.info(f"Status: {status}")
    
    async def start(self):
        if self.ftms:
            await self.ftms.start_or_resume()
        logger.info("Started/Resumed")
    
    async def pause(self):
        if self.ftms:
            await self.ftms.stop_or_pause(pause=True)
        logger.info("Paused")
    
    async def stop(self):
        if self.ftms:
            await self.ftms.stop_or_pause(pause=False)
        logger.info("Stopped")
    
    async def set_resistance(self, level: int):
        """设置阻力 (UI值0-100，转换为0-255)
        
        pycycling 只支持 1 字节 (0-255)
        ftms_server 负责转换为硬件档位 1-24
        """
        ftms_value = int(level * 2.55 + 0.5)  # 四舍五入，100 -> 255
        ftms_value = min(255, ftms_value)  # 确保不超过 255
        logger.info(f"Setting resistance: UI={level}, FTMS={ftms_value}")
        
        if self.ftms:
            try:
                await self.ftms.set_target_resistance_level(ftms_value)
                logger.info(f"Resistance command sent: {ftms_value}")
            except Exception as e:
                logger.error(f"Failed to set resistance: {e}")
                import traceback
                traceback.print_exc()
        else:
            logger.error("FTMS service not initialized")
    
    async def set_incline(self, grade: float):
        if self.ftms:
            await self.ftms.set_simulation_parameters(int(grade * 100), 0, 0, 0)
        logger.info(f"Set incline to {grade}%")


async def scan_devices(name: str) -> Optional[str]:
    logger.info(f"Looking for: {name}")
    devices = await BleakScanner.discover(timeout=5.0)
    for device in devices:
        if device.name and name.lower() in device.name.lower():
            logger.info(f"Found: {device.name} ({device.address})")
            return device.address
    return None


async def create_web_app(ftms_client: FTMSClient):
    async def get_data(request):
        return web.json_response(ftms_client.current_data)
    
    async def post_control(request):
        data = await request.json()
        cmd = data.get("command")
        
        try:
            if cmd == "start":
                await ftms_client.start()
                return web.json_response({"status": "ok", "message": "Started"})
            elif cmd == "pause":
                await ftms_client.pause()
                return web.json_response({"status": "ok", "message": "Paused"})
            elif cmd == "stop":
                await ftms_client.stop()
                return web.json_response({"status": "ok", "message": "Stopped"})
            elif cmd == "incline":
                grade = float(data.get("grade", 0))
                await ftms_client.set_incline(grade)
                return web.json_response({"status": "ok", "message": f"Incline set to {grade}%"})
            elif cmd == "resistance":
                level = int(data.get("level", 10))
                await ftms_client.set_resistance(level)
                return web.json_response({"status": "ok", "message": f"Resistance set to {level}"})
            else:
                return web.json_response({"status": "error", "message": "Unknown command"}, status=400)
        except Exception as e:
            return web.json_response({"status": "error", "message": str(e)}, status=500)
    
    async def index(request):
        html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BikeCon FTMS Test</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background: #1a1a2e;
            color: #eee;
        }
        h1 { text-align: center; color: #00d4ff; }
        
        .data-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
            margin: 20px 0;
        }
        
        .data-card {
            background: #16213e;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }
        
        .data-card .value {
            font-size: 2.5em;
            font-weight: bold;
            color: #00d4ff;
        }
        
        .data-card .label {
            color: #888;
            font-size: 0.9em;
            margin-top: 5px;
        }
        
        .controls {
            background: #16213e;
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
        }
        
        .btn-group {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }
        
        button {
            flex: 1;
            padding: 15px;
            border: none;
            border-radius: 8px;
            font-size: 1em;
            cursor: pointer;
            transition: transform 0.1s;
        }
        
        button:active { transform: scale(0.98); }
        
        .btn-start { background: #00d4ff; color: #000; }
        .btn-pause { background: #ffaa00; color: #fff; }
        .btn-stop { background: #ff6b6b; color: #fff; }
        
        .slider-container {
            margin: 15px 0;
        }
        
        .slider-container label {
            display: block;
            margin-bottom: 5px;
            color: #888;
        }
        
        input[type="range"] {
            width: 100%;
            height: 25px;
            border-radius: 5px;
            background: #0f3460;
        }
        
        .status {
            text-align: center;
            padding: 10px;
            border-radius: 5px;
            margin-top: 20px;
        }
        
        .status.connected { background: #00d4ff22; color: #00d4ff; }
        .status.disconnected { background: #ff6b6b22; color: #ff6b6b; }
    </style>
</head>
<body>
    <h1>BikeCon FTMS Test</h1>
    
    <div id="status" class="status disconnected">Disconnected</div>
    
    <div class="data-grid">
        <div class="data-card">
            <div class="value" id="cadence">0</div>
            <div class="label">Cadence (RPM)</div>
        </div>
        <div class="data-card">
            <div class="value" id="power">0</div>
            <div class="label">Power (W)</div>
        </div>
        <div class="data-card">
            <div class="value" id="speed">0.0</div>
            <div class="label">Speed (km/h)</div>
        </div>
        <div class="data-card">
            <div class="value" id="distance">0</div>
            <div class="label">Distance (m)</div>
        </div>
    </div>
    
        <div class="controls">
        <div class="btn-group">
            <button class="btn-start" onclick="sendCommand('start')">Start</button>
            <button class="btn-pause" onclick="sendCommand('pause')">Pause</button>
            <button class="btn-stop" onclick="sendCommand('stop')">Stop</button>
        </div>
        
        <div class="slider-container">
            <label>Incline: <span id="inclineVal">0</span>%</label>
            <input type="range" id="incline" min="-10" max="15" value="0" oninput="document.getElementById('inclineVal').textContent = this.value">
            <button onclick="setIncline()">Set Incline</button>
        </div>
        
        <div class="slider-container">
            <label>Resistance: <span id="resVal">10</span>%</label>
            <input type="range" id="resistance" min="0" max="100" value="10" oninput="document.getElementById('resVal').textContent = this.value">
            <button onclick="setResistance()">Set Resistance</button>
        </div>
    </div>
    
    <script>
        async function updateData() {
            try {
                const resp = await fetch('/data');
                const data = await resp.json();
                
                document.getElementById('cadence').textContent = data.cadence;
                document.getElementById('power').textContent = data.power;
                document.getElementById('speed').textContent = data.speed.toFixed(1);
                document.getElementById('distance').textContent = data.distance;
                
                const status = document.getElementById('status');
                if (data.status === 'connected') {
                    status.textContent = 'Connected';
                    status.className = 'status connected';
                } else {
                    status.textContent = 'Disconnected';
                    status.className = 'status disconnected';
                }
            } catch (e) {
                console.error(e);
            }
        }
        
        async function sendCommand(cmd) {
            try {
                await fetch('/control', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({command: cmd})
                });
            } catch (e) {
                console.error(e);
            }
        }
        
        async function setIncline() {
            const grade = document.getElementById('incline').value;
            try {
                await fetch('/control', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({command: 'incline', grade: parseFloat(grade)})
                });
            } catch (e) {
                console.error(e);
            }
        }
        
        async function setResistance() {
            const level = document.getElementById('resistance').value;
            try {
                await fetch('/control', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({command: 'resistance', level: parseInt(level)})
                });
            } catch (e) {
                console.error(e);
            }
        }
        
        setInterval(updateData, 1000);
        updateData();
    </script>
</body>
</html>"""
        return web.Response(text=html, content_type='text/html')
    
    app = web.Application()
    app.router.add_get('/data', get_data)
    app.router.add_post('/control', post_control)
    app.router.add_get('/', index)
    return app


async def run_web_server(ftms_client: FTMSClient, port: int = 8080):
    app = await create_web_app(ftms_client)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web Server started at http://localhost:{port}")
    
    while ftms_client.running:
        await asyncio.sleep(1)


async def interactive_mode(address: str):
    """Interactive console mode"""
    logger.info(f"Connecting to {address}...")
    
    client = FTMSClient(address)
    
    try:
        await client.connect()
        
        logger.info("==================== Interactive Mode ====================")
        logger.info("Commands:")
        logger.info("  s           - Start/Resume")
        logger.info("  p           - Pause")
        logger.info("  i <grade>   - Set incline (e.g., i 5)")
        logger.info("  r <level>   - Set resistance (e.g., r 10)")
        logger.info("  q           - Quit")
        logger.info("================================================================")
        
        while client.running:
            try:
                cmd = await asyncio.get_event_loop().run_in_executor(None, input, "\nCommand> ")
                cmd = cmd.strip()
                
                if not cmd:
                    continue
                
                if cmd.lower() == 'q':
                    break
                elif cmd.lower() == 's':
                    await client.start()
                elif cmd.lower() == 'p':
                    await client.pause()
                elif cmd.lower().startswith('i '):
                    try:
                        grade = float(cmd.split()[1])
                        await client.set_incline(grade)
                    except (IndexError, ValueError):
                        logger.info("Usage: i <grade>")
                elif cmd.lower().startswith('r '):
                    try:
                        level = int(cmd.split()[1])
                        await client.set_resistance(level)
                    except (IndexError, ValueError):
                        logger.info("Usage: r <level>")
                else:
                    logger.info("Unknown command")
                    
            except EOFError:
                break
            except Exception as e:
                logger.error(f"Error: {e}")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()


async def main():
    parser = argparse.ArgumentParser(description="FTMS Client Test with Web UI")
    parser.add_argument("device", nargs="?", default="BikeCon-FTMS", help="Device name or MAC address")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Web server port")
    parser.add_argument("--no-web", action="store_true", help="Disable web interface")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive console mode")
    args = parser.parse_args()
    
    address = args.device
    
    if len(address.split(':')) != 6:
        found = await scan_devices(address)
        if not found:
            logger.error("Device not found. Please specify MAC address manually.")
            sys.exit(1)
        address = found
    
    logger.info(f"Target: {address}")
    
    if args.interactive:
        await interactive_mode(address)
        return
    
    client = FTMSClient(address)
    
    try:
        await client.connect()
        
        if args.no_web:
            logger.info("Running in console mode. Press Ctrl+C to exit.")
            while client.running:
                await asyncio.sleep(1)
        else:
            logger.info(f"Starting web interface on port {args.port}")
            await run_web_server(client, args.port)
            
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if client.client:
            await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Exited")
