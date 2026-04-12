#!/usr/bin/env python3
"""
Fake FTMS Bike Server for BikeCon integration testing.

Usage:
    python3 debug/fake_ftms_server.py
    python3 debug/fake_ftms_server.py --name BikeCon-Fake-FTMS --hz 5

Then set BikeCon to ftms driver and point identity.json.bike_mac to this fake BLE device.
"""

import argparse
import asyncio
import json
import random
import signal
import struct
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from aiohttp import web

from bless import (
    BlessServer,
    BlessGATTCharacteristic,
    GATTCharacteristicProperties,
    GATTAttributePermissions,
)


class FTMSOpCode(Enum):
    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_RESISTANCE = 0x04
    SET_TARGET_POWER = 0x06
    START_RESUME = 0x07
    PAUSE_STOP = 0x08
    SET_INDOOR_BIKE_SIMULATION = 0x11


# Standard FTMS UUIDs
FTMS_UUID = "00001826-0000-1000-8000-00805f9b34fb"
FTM_FEATURE_UUID = "00002acc-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA_UUID = "00002ad2-0000-1000-8000-00805f9b34fb"
FTM_CONTROL_POINT_UUID = "00002ad9-0000-1000-8000-00805f9b34fb"
FTM_STATUS_UUID = "00002ada-0000-1000-8000-00805f9b34fb"
SUPPORTED_RESISTANCE_LEVEL_RANGE_UUID = "00002ad6-0000-1000-8000-00805f9b34fb"

DEVICE_INFO_SERVICE = "0000180A-0000-1000-8000-00805f9b34fb"
CHAR_MANUFACTURER_NAME = "00002A29-0000-1000-8000-00805f9b34fb"
CHAR_MODEL_NUMBER = "00002A24-0000-1000-8000-00805f9b34fb"


@dataclass
class FakeBikeState:
    resistance_level: int = 8
    cadence_rpm: float = 0.0
    power_w: float = 0.0
    speed_kmh: float = 0.0
    distance_m: float = 0.0
    calories_kcal: float = 0.0
    elapsed_s: float = 0.0

    mode: str = "ready"  # ready | active | paused
    control_granted: bool = False
    target_power_w: Optional[int] = None


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fake FTMS Bike Control</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }
        h1 { color: #00d4ff; }
        .card { background: #16213e; padding: 20px; border-radius: 10px; margin-bottom: 15px; }
        .row { display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 15px; }
        .stat { background: #0f3460; padding: 15px; border-radius: 8px; text-align: center; min-width: 100px; }
        .stat-label { font-size: 12px; color: #888; }
        .stat-value { font-size: 24px; font-weight: bold; color: #00d4ff; }
        button { background: #00d4ff; color: #000; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 14px; margin: 5px; }
        button:hover { background: #00b8e6; }
        button.stop { background: #ff4757; color: #fff; }
        button.active { background: #2ed573; }
        input, select { padding: 8px; border-radius: 5px; border: 1px solid #333; background: #0f3460; color: #fff; }
        .mode-active { color: #2ed573; }
        .mode-paused { color: #ffa502; }
        .mode-ready { color: #aaa; }
    </style>
</head>
<body>
    <h1>Fake FTMS Bike</h1>
    <div class="card">
        <h3>Status</h3>
        <div class="row">
            <div class="stat"><div class="stat-label">Mode</div><div class="stat-value" id="mode">ready</div></div>
            <div class="stat"><div class="stat-label">Cadence</div><div class="stat-value" id="cadence">0.0</div></div>
            <div class="stat"><div class="stat-label">Power</div><div class="stat-value" id="power">0</div></div>
            <div class="stat"><div class="stat-label">Speed</div><div class="stat-value" id="speed">0.0</div></div>
            <div class="stat"><div class="stat-label">Resistance</div><div class="stat-value" id="resistance">0</div></div>
        </div>
        <div class="row">
            <div class="stat"><div class="stat-label">Distance (m)</div><div class="stat-value" id="distance">0</div></div>
            <div class="stat"><div class="stat-label">Calories</div><div class="stat-value" id="calories">0</div></div>
            <div class="stat"><div class="stat-label">Time (s)</div><div class="stat-value" id="time">0</div></div>
            <div class="stat"><div class="stat-label">Target Power</div><div class="stat-value" id="target_power">--</div></div>
        </div>
    </div>
    <div class="card">
        <h3>Controls</h3>
        <button onclick="sendCmd('start')">Start/Resume</button>
        <button class="stop" onclick="sendCmd('pause')">Pause</button>
        <button class="stop" onclick="sendCmd('stop')">Stop</button>
        <button onclick="sendCmd('reset')">Reset</button>
    </div>
    <div class="card">
        <h3>Set Resistance (1-24)</h3>
        <input type="number" id="resistanceInput" min="1" max="24" value="8">
        <button onclick="setResistance()">Set</button>
    </div>
    <div class="card">
        <h3>Set Target Power (30-800W)</h3>
        <input type="number" id="powerInput" min="30" max="800" value="150">
        <button onclick="setPower()">Set</button>
    </div>
    <div class="card">
        <h3>Indoor Bike Simulation</h3>
        <label>Grade: <input type="number" id="gradeInput" value="0" step="0.1">%</label>
        <button onclick="setSimulation()">Set</button>
    </div>
    <script>
        function updateState(data) {
            document.getElementById('mode').textContent = data.mode;
            document.getElementById('mode').className = 'stat-value mode-' + data.mode;
            document.getElementById('cadence').textContent = data.cadence_rpm.toFixed(1);
            document.getElementById('power').textContent = Math.round(data.power_w);
            document.getElementById('speed').textContent = data.speed_kmh.toFixed(1);
            document.getElementById('resistance').textContent = data.resistance_level;
            document.getElementById('distance').textContent = Math.round(data.distance_m);
            document.getElementById('calories').textContent = Math.round(data.calories_kcal);
            document.getElementById('time').textContent = Math.round(data.elapsed_s);
            document.getElementById('target_power').textContent = data.target_power_w || '--';
        }

        function sendCmd(cmd) {
            fetch('/cmd', { method: 'POST', body: JSON.stringify({ cmd }), headers: {'Content-Type': 'application/json'} });
        }

        function setResistance() {
            const level = parseInt(document.getElementById('resistanceInput').value);
            fetch('/cmd', { method: 'POST', body: JSON.stringify({ cmd: 'resistance', level }), headers: {'Content-Type': 'application/json'} });
        }

        function setPower() {
            const watts = parseInt(document.getElementById('powerInput').value);
            fetch('/cmd', { method: 'POST', body: JSON.stringify({ cmd: 'power', watts }), headers: {'Content-Type': 'application/json'} });
        }

        function setSimulation() {
            const grade = parseFloat(document.getElementById('gradeInput').value);
            fetch('/cmd', { method: 'POST', body: JSON.stringify({ cmd: 'simulation', grade }), headers: {'Content-Type': 'application/json'} });
        }

        async function poll() {
            try {
                const r = await fetch('/state');
                const data = await r.json();
                updateState(data);
            } catch(e) {}
        }
        setInterval(poll, 500);
        poll();
    </script>
</body>
</html>
"""


class FakeFTMSServer:
    def __init__(self, name: str, hz: float, seed: int, web_port: int):
        self.name = name
        self.hz = max(1.0, float(hz))
        self.tick_sec = 1.0 / self.hz
        self.web_port = web_port
        self.server: Optional[BlessServer] = None
        self.running = False
        self.state = FakeBikeState()
        self._app = web.Application()

        self._rng = random.Random(seed)
        self._last_print_ts = 0.0
        self._print_interval = 2.0

        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/state", self._handle_state)
        self._app.router.add_post("/cmd", self._handle_cmd)

    async def _handle_index(self, request):
        return web.Response(text=HTML_TEMPLATE, content_type="text/html")

    async def _handle_state(self, request):
        s = self.state
        return web.json_response({
            "mode": s.mode,
            "resistance_level": s.resistance_level,
            "cadence_rpm": s.cadence_rpm,
            "power_w": s.power_w,
            "speed_kmh": s.speed_kmh,
            "distance_m": s.distance_m,
            "calories_kcal": s.calories_kcal,
            "elapsed_s": s.elapsed_s,
            "target_power_w": s.target_power_w,
        })

    async def _handle_cmd(self, request):
        data = await request.json()
        cmd = data.get("cmd")

        if cmd == "start":
            await self._ctrl_start_resume()
        elif cmd == "pause":
            await self._ctrl_pause()
        elif cmd == "stop":
            await self._ctrl_stop()
        elif cmd == "reset":
            await self._ctrl_reset()
        elif cmd == "resistance":
            await self._ctrl_set_resistance(data.get("level", 8))
        elif cmd == "power":
            await self._ctrl_set_power(data.get("watts", 150))
        elif cmd == "simulation":
            await self._ctrl_set_simulation(data.get("grade", 0))

        return web.json_response({"ok": True})

    async def _ctrl_start_resume(self):
        self.on_control_point_write(None, bytearray([FTMSOpCode.START_RESUME.value]))

    async def _ctrl_pause(self):
        self.on_control_point_write(None, bytearray([FTMSOpCode.PAUSE_STOP.value, 0x02]))

    async def _ctrl_stop(self):
        self.on_control_point_write(None, bytearray([FTMSOpCode.PAUSE_STOP.value, 0x01]))

    async def _ctrl_reset(self):
        self.on_control_point_write(None, bytearray([FTMSOpCode.RESET.value]))

    async def _ctrl_set_resistance(self, level: int):
        level = max(1, min(24, int(level)))
        raw = level * 10
        self.on_control_point_write(None, bytearray([FTMSOpCode.SET_TARGET_RESISTANCE.value, raw]))

    async def _ctrl_set_power(self, watts: int):
        watts = max(30, min(800, int(watts)))
        raw = struct.pack("<h", watts)
        self.on_control_point_write(None, bytearray([FTMSOpCode.SET_TARGET_POWER.value]) + raw)

    async def _ctrl_set_simulation(self, grade: float):
        grade_raw = int(grade * 100)
        data = bytearray([FTMSOpCode.SET_INDOOR_BIKE_SIMULATION.value]) + struct.pack("<hhBB", 0, grade_raw, 0, 0)
        self.on_control_point_write(None, data)

    def _log(self, msg: str):
        print(f"[FakeFTMS] {msg}")

    async def _setup_services(self):
        await self.server.add_new_service(FTMS_UUID)

        # Fitness Machine Feature / Target Setting Feature
        # Keep it broad enough for common clients.
        feature_val = 0x00005307
        target_val = 0x0000000C
        await self.server.add_new_characteristic(
            FTMS_UUID,
            FTM_FEATURE_UUID,
            GATTCharacteristicProperties.read,
            struct.pack("<I", feature_val) + struct.pack("<I", target_val),
            GATTAttributePermissions.readable,
        )

        await self.server.add_new_characteristic(
            FTMS_UUID,
            INDOOR_BIKE_DATA_UUID,
            GATTCharacteristicProperties.notify,
            bytearray([0] * 20),
            GATTAttributePermissions.readable,
        )

        await self.server.add_new_characteristic(
            FTMS_UUID,
            FTM_STATUS_UUID,
            GATTCharacteristicProperties.notify,
            bytearray([0x00]),
            GATTAttributePermissions.readable,
        )

        await self.server.add_new_characteristic(
            FTMS_UUID,
            FTM_CONTROL_POINT_UUID,
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.indicate,
            bytearray([0x00]),
            GATTAttributePermissions.writeable,
        )

        # min=0, max=24.0(240), step=0.5(5)
        await self.server.add_new_characteristic(
            FTMS_UUID,
            SUPPORTED_RESISTANCE_LEVEL_RANGE_UUID,
            GATTCharacteristicProperties.read,
            struct.pack("<hhH", 0, 240, 5),
            GATTAttributePermissions.readable,
        )

        await self.server.add_new_service(DEVICE_INFO_SERVICE)
        await self.server.add_new_characteristic(
            DEVICE_INFO_SERVICE,
            CHAR_MANUFACTURER_NAME,
            GATTCharacteristicProperties.read,
            b"BikeCon-Debug",
            GATTAttributePermissions.readable,
        )
        await self.server.add_new_characteristic(
            DEVICE_INFO_SERVICE,
            CHAR_MODEL_NUMBER,
            GATTCharacteristicProperties.read,
            b"FakeFTMSBike",
            GATTAttributePermissions.readable,
        )

    def on_read(self, characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
        return characteristic.value

    def _send_cp_response(self, opcode: int, code: int = 0x01):
        resp = bytearray([0x80, opcode & 0xFF, code & 0xFF])
        try:
            self.server.get_characteristic(FTM_CONTROL_POINT_UUID).value = resp
            self.server.update_value(FTMS_UUID, FTM_CONTROL_POINT_UUID)
        except Exception as e:
            self._log(f"Control point indication failed: {e}")

    def _notify_status(self, status_opcode: int, p1: int = 0x00):
        # Minimal status payload, enough for diagnostics.
        payload = bytearray([status_opcode & 0xFF, p1 & 0xFF])
        try:
            self.server.get_characteristic(FTM_STATUS_UUID).value = payload
            self.server.update_value(FTMS_UUID, FTM_STATUS_UUID)
        except Exception as e:
            self._log(f"FTM status notify failed: {e}")

    def _set_mode(self, mode: str):
        if mode not in ("ready", "active", "paused"):
            return
        if self.state.mode == mode:
            return
        self.state.mode = mode
        self._log(f"Mode -> {mode}")

    def _calc_resistance_from_grade(self, grade_pct: float) -> int:
        level = round(6 + grade_pct * 1.5)
        return max(1, min(24, level))

    def on_control_point_write(self, characteristic: BlessGATTCharacteristic, value: Any, **kwargs):
        data = bytearray(value) if isinstance(value, (bytes, bytearray)) else bytearray(value)
        if not data:
            return

        opcode = data[0]
        self._log(f"CP write opcode=0x{opcode:02X} data={data.hex()}")

        try:
            if opcode == FTMSOpCode.REQUEST_CONTROL.value:
                self.state.control_granted = True
                self._send_cp_response(opcode, 0x01)
                self._notify_status(0x00)
                return

            if opcode == FTMSOpCode.RESET.value:
                self.state.cadence_rpm = 0.0
                self.state.power_w = 0.0
                self.state.speed_kmh = 0.0
                self.state.distance_m = 0.0
                self.state.calories_kcal = 0.0
                self.state.elapsed_s = 0.0
                self.state.target_power_w = None
                self._set_mode("ready")
                self._send_cp_response(opcode, 0x01)
                self._notify_status(0x01)
                return

            if opcode == FTMSOpCode.START_RESUME.value:
                self._set_mode("active")
                self._send_cp_response(opcode, 0x01)
                self._notify_status(0x04)
                return

            if opcode == FTMSOpCode.PAUSE_STOP.value:
                param = data[1] if len(data) >= 2 else 0
                if param == 0x02:
                    self._set_mode("paused")
                    self._send_cp_response(opcode, 0x01)
                    self._notify_status(0x02)
                else:
                    self._set_mode("ready")
                    self.state.target_power_w = None
                    self._send_cp_response(opcode, 0x01)
                    self._notify_status(0x01)
                return

            if opcode == FTMSOpCode.SET_TARGET_RESISTANCE.value:
                if len(data) < 2:
                    self._send_cp_response(opcode, 0x03)
                    return
                ftms_raw = data[1]
                # FTMS 0-255 ~= 0.0-25.5, map to 1..24.
                level = max(1, min(24, int(round(ftms_raw / 10.0))))
                self.state.resistance_level = level
                self.state.target_power_w = None
                self._send_cp_response(opcode, 0x01)
                self._notify_status(0x05, level)
                self._log(f"Set resistance -> L{level} (raw={ftms_raw})")
                return

            if opcode == FTMSOpCode.SET_TARGET_POWER.value:
                if len(data) < 3:
                    self._send_cp_response(opcode, 0x03)
                    return
                watts = int(struct.unpack("<h", data[1:3])[0])
                watts = max(30, min(800, watts))
                self.state.target_power_w = watts
                self._send_cp_response(opcode, 0x01)
                self._notify_status(0x08)
                self._log(f"Set target power -> {watts}W")
                return

            if opcode == FTMSOpCode.SET_INDOOR_BIKE_SIMULATION.value:
                if len(data) < 7:
                    self._send_cp_response(opcode, 0x03)
                    return
                wind_raw, grade_raw, crr_raw, cw_raw = struct.unpack("<hhBB", data[1:7])
                grade_pct = grade_raw * 0.01
                level = self._calc_resistance_from_grade(grade_pct)
                self.state.resistance_level = level
                self.state.target_power_w = None
                self._send_cp_response(opcode, 0x01)
                self._notify_status(0x12)
                self._log(
                    f"Simulation grade={grade_pct:.1f}% wind={wind_raw*0.01:.1f}m/s crr={crr_raw*0.0001:.4f} -> L{level}"
                )
                return

            self._send_cp_response(opcode, 0x02)
        except Exception as e:
            self._log(f"Control point parse error: {e}")
            self._send_cp_response(opcode, 0x04)

    def _simulate_tick(self):
        s = self.state

        if s.mode == "active":
            base_rpm = 68 + s.resistance_level * 1.8
            cadence = base_rpm + self._rng.uniform(-4.0, 4.0)
            cadence = max(35.0, min(125.0, cadence))

            if s.target_power_w is not None:
                power = float(s.target_power_w + self._rng.uniform(-8.0, 8.0))
            else:
                power = cadence * (0.7 + s.resistance_level * 0.11)
                power += self._rng.uniform(-10.0, 10.0)

            power = max(30.0, min(900.0, power))
            speed = max(8.0, min(65.0, cadence * 0.33 + self._rng.uniform(-1.2, 1.2)))

            s.cadence_rpm = cadence
            s.power_w = power
            s.speed_kmh = speed

            s.distance_m += speed * 1000.0 / 3600.0 * self.tick_sec
            # Very rough kcal model
            s.calories_kcal += (power * self.tick_sec) / 4186.0
            s.elapsed_s += self.tick_sec

        elif s.mode == "paused":
            s.cadence_rpm = max(0.0, s.cadence_rpm - 7.5)
            s.power_w = max(0.0, s.power_w - 30.0)
            s.speed_kmh = max(0.0, s.speed_kmh - 2.0)
        else:
            s.cadence_rpm = max(0.0, s.cadence_rpm - 9.0)
            s.power_w = max(0.0, s.power_w - 35.0)
            s.speed_kmh = max(0.0, s.speed_kmh - 2.6)

    def _build_indoor_bike_data(self) -> bytearray:
        s = self.state

        # Flags match BikeCon bike_driver_ftms parser expectations.
        # bits: cadence(2), distance(4), resistance(5), power(6), energy(8), elapsed(11)
        # plus instantaneous speed present when bit0=0.
        flags = 0x0974

        speed_raw = int(max(0, min(65535, round(s.speed_kmh * 100.0))))
        cadence_raw = int(max(0, min(65535, round(s.cadence_rpm * 2.0))))
        dist_24 = int(max(0, min(0xFFFFFF, round(s.distance_m))))
        resistance_raw = int(max(-32768, min(32767, round(s.resistance_level * 10.0))))
        power_raw = int(max(-32768, min(32767, round(s.power_w))))
        kcal_total = int(max(0, min(65535, round(s.calories_kcal))))
        kcal_per_hour = int(max(0, min(65535, round(s.power_w * 0.86))))
        kcal_per_min = int(max(0, min(255, round(kcal_per_hour / 60.0))))
        elapsed = int(max(0, min(65535, round(s.elapsed_s))))

        payload = bytearray()
        payload += struct.pack("<H", flags)
        payload += struct.pack("<H", speed_raw)
        payload += struct.pack("<H", cadence_raw)
        payload += struct.pack("<I", dist_24)[:3]
        payload += struct.pack("<h", resistance_raw)
        payload += struct.pack("<h", power_raw)
        payload += struct.pack("<H", kcal_total)
        payload += struct.pack("<H", kcal_per_hour)
        payload += struct.pack("<B", kcal_per_min)
        payload += struct.pack("<H", elapsed)

        return payload

    def _notify_indoor_bike_data(self):
        payload = self._build_indoor_bike_data()
        try:
            self.server.get_characteristic(INDOOR_BIKE_DATA_UUID).value = payload
            self.server.update_value(FTMS_UUID, INDOOR_BIKE_DATA_UUID)
        except Exception as e:
            self._log(f"Indoor bike data notify failed: {e}")

    async def _broadcast_loop(self):
        self._log(f"Broadcast loop running at {self.hz:.1f} Hz")
        while self.running:
            self._simulate_tick()
            self._notify_indoor_bike_data()

            now = time.monotonic()
            if now - self._last_print_ts >= self._print_interval:
                self._last_print_ts = now
                s = self.state
                self._log(
                    f"state={s.mode:<6} rpm={s.cadence_rpm:5.1f} pwr={s.power_w:6.1f}W "
                    f"spd={s.speed_kmh:5.1f}km/h res=L{s.resistance_level:02d} dist={s.distance_m:7.1f}m"
                )

            await asyncio.sleep(self.tick_sec)

    async def start(self):
        if self.running:
            return

        self.server = BlessServer(name=self.name, loop=asyncio.get_running_loop())
        self.server.read_request_func = self.on_read
        self.server.write_request_func = self.on_control_point_write

        await self._setup_services()
        await self.server.start()

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.web_port)
        await self._site.start()

        self.running = True
        self._log(f"Started BLE fake FTMS bike: {self.name}")
        self._log(f"Web UI: http://localhost:8080")

    async def stop(self):
        if not self.running:
            return
        self.running = False

        if hasattr(self, "_site"):
            await self._site.stop()
            await self._runner.cleanup()

        if self.server:
            try:
                await self.server.stop()
            except Exception as e:
                self._log(f"Server stop error: {e}")
        self._log("Stopped")


async def amain(args):
    fake = FakeFTMSServer(name=args.name, hz=args.hz, seed=args.seed, web_port=args.web_port)

    stop_event = asyncio.Event()

    def _handle_stop():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_stop)
        except NotImplementedError:
            pass

    await fake.start()

    if args.start_active:
        fake.state.mode = "active"

    task = asyncio.create_task(fake._broadcast_loop())

    fake._log("Ready. Use BikeCon web controls to send FTMS control commands.")
    fake._log("Hint: if needed, set identity.json.bike_mac to this fake BLE address from scanner output.")

    try:
        await stop_event.wait()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await fake.stop()


def parse_args():
    p = argparse.ArgumentParser(description="Fake FTMS bike server for BikeCon debugging")
    p.add_argument("--name", default="BikeCon-Fake-FTMS", help="BLE device name")
    p.add_argument("--hz", type=float, default=4.0, help="IndoorBikeData notify rate (Hz)")
    p.add_argument("--seed", type=int, default=7, help="Random seed for repeatable simulation")
    p.add_argument("--web-port", type=int, default=8080, help="Web UI port")
    p.add_argument("--start-active", action="store_true", help="Start in ACTIVE mode immediately")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(amain(parse_args()))
