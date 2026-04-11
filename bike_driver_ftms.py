import asyncio
import struct
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from bleak import BleakClient


def to_int(val):
    if isinstance(val, (bytes, bytearray)):
        return int.from_bytes(val, "little") if len(val) <= 4 else 0
    return int(val) if isinstance(val, (int, float)) else 0


class BikeStatus(Enum):
    UNKNOWN = 0
    READY = 1
    TRANSITION = 2
    ACTIVE = 3
    PAUSED = 4


@dataclass
class BikeData:
    rpm: int = 0
    power: int = 0
    duration: int = 0
    distance: int = 0
    speed: float = 0.0
    resistance: int = 0
    calories: float = 0.0
    status_code: int = 0
    raw_data: Optional[str] = None


class BikeClient:
    INDOOR_BIKE_DATA_UUID = "00002ad2-0000-1000-8000-00805f9b34fb"
    FTM_CONTROL_POINT_UUID = "00002ad9-0000-1000-8000-00805f9b34fb"
    FTM_STATUS_UUID = "00002ada-0000-1000-8000-00805f9b34fb"

    HEARTBEAT_INTERVAL = 1.0
    DATA_TIMEOUT_LIMIT = 20.0
    RECONNECT_INTERVAL_SEC = 5.0
    CONTROL_SEND_INTERVAL = 0.12

    def __init__(
        self,
        mac_address: str,
        data_callback: Callable[[BikeData], None],
        status_callback: Optional[Callable[[BikeStatus, BikeStatus], None]] = None,
    ):
        self.mac_address = mac_address
        self.data_callback = data_callback
        self.status_callback = status_callback

        self.client: Optional[BleakClient] = None
        # track last sent control payload (used to interpret CP indications)
        self._last_control_sent: Optional[bytes] = None
        self.running = False

        self._current_status = BikeStatus.READY
        self._last_data_time = time.time()
        self._control_requested = False
        self._connect_generation = 0

        self._watchdog_task: Optional[asyncio.Task] = None
        self._manager_task: Optional[asyncio.Task] = None
        self._tx_worker_task: Optional[asyncio.Task] = None
        self._tx_queue: asyncio.Queue[bytes] = asyncio.Queue()

        self._duration = 0
        self._distance = 0
        self._power = 0
        self._rpm = 0
        self._current_resistance = 1
        self._calories = 0.0
        self._speed = 0.0
        self._status = BikeStatus.READY.value

    def _log(self, msg: str):
        print(f"[BikeDriver-FTMS] {msg}")

    def _set_status(self, new_status: BikeStatus):
        if new_status == self._current_status:
            return
        old_status = self._current_status
        self._current_status = new_status
        self._status = new_status.value
        if self.status_callback:
            self.status_callback(old_status, new_status)

    def _normalize_resistance(self, raw: int) -> int:
        # Some devices use 0.1 precision, others use integral levels.
        if raw > 24:
            val = round(raw / 10.0)
        else:
            val = raw
        return max(0, int(val))

    def _parse_indoor_bike_data(self, data: bytearray) -> BikeData:
        payload = bytes(data)
        if len(payload) < 2:
            raise ValueError("Indoor Bike Data payload too short")

        flags = struct.unpack_from("<H", payload, 0)[0]
        ptr = 2

        speed = 0.0
        rpm = 0
        distance = self._distance
        resistance = self._current_resistance
        power = 0
        calories = self._calories
        duration = self._duration

        more_data = bool(flags & (1 << 0))

        if not more_data and ptr + 2 <= len(payload):
            speed_raw = struct.unpack_from("<H", payload, ptr)[0]
            speed = speed_raw / 100.0
            ptr += 2

        if flags & (1 << 1) and ptr + 2 <= len(payload):
            ptr += 2  # average speed

        if flags & (1 << 2) and ptr + 2 <= len(payload):
            cadence_raw = struct.unpack_from("<H", payload, ptr)[0]
            rpm = int(round(cadence_raw / 2.0))
            ptr += 2

        if flags & (1 << 3) and ptr + 2 <= len(payload):
            ptr += 2  # average cadence

        if flags & (1 << 4) and ptr + 3 <= len(payload):
            dist_24 = payload[ptr] | (payload[ptr + 1] << 8) | (payload[ptr + 2] << 16)
            distance = int(dist_24)
            ptr += 3

        if flags & (1 << 5) and ptr + 2 <= len(payload):
            res_raw = struct.unpack_from("<h", payload, ptr)[0]
            resistance = self._normalize_resistance(res_raw)
            ptr += 2

        if flags & (1 << 6) and ptr + 2 <= len(payload):
            power = int(struct.unpack_from("<h", payload, ptr)[0])
            ptr += 2

        if flags & (1 << 7) and ptr + 2 <= len(payload):
            ptr += 2  # average power

        if flags & (1 << 8) and ptr + 5 <= len(payload):
            total_energy = struct.unpack_from("<H", payload, ptr)[0]
            calories = float(total_energy)
            ptr += 5  # total + per hour + per minute

        if flags & (1 << 9) and ptr + 1 <= len(payload):
            ptr += 1  # heart rate

        if flags & (1 << 10) and ptr + 1 <= len(payload):
            ptr += 1  # metabolic equivalent

        if flags & (1 << 11) and ptr + 2 <= len(payload):
            duration = int(struct.unpack_from("<H", payload, ptr)[0])
            ptr += 2

        if flags & (1 << 12) and ptr + 2 <= len(payload):
            ptr += 2  # remaining time

        # Do NOT infer status from instantaneous indoor data here.
        # Status should be determined by explicit control events or status notifications.
        status = self._current_status

        return BikeData(
            rpm=to_int(rpm),
            power=to_int(power),
            duration=to_int(duration),
            distance=to_int(distance),
            speed=round(float(speed), 1),
            resistance=to_int(resistance),
            calories=float(calories),
            status_code=status.value,
            raw_data=payload.hex(),
        )

    def _on_disconnected(self, _client: BleakClient):
        self._log("BLE disconnected callback fired")

    def _on_indoor_bike_data(self, sender: Any, data: bytearray):
        self._last_data_time = time.time()
        try:
            bike_data = self._parse_indoor_bike_data(data)
        except Exception as e:
            self._log(f"Indoor Bike Data parse failed: {e}")
            return

        # Update measurement fields only; do NOT change driver status here.
        self._rpm = bike_data.rpm
        self._power = bike_data.power
        self._duration = bike_data.duration
        self._distance = bike_data.distance
        self._calories = bike_data.calories
        self._speed = bike_data.speed
        self._current_resistance = bike_data.resistance

        # Broadcast measured data. Status changes must come from control/status paths.
        self.data_callback(bike_data)

    def _on_control_point_indication(self, sender: Any, data: bytearray):
        payload = bytes(data)
        if len(payload) >= 3 and payload[0] == 0x80:
            req_opcode = payload[1]
            result_code = payload[2]
            self._log(f"ControlPoint rsp opcode=0x{req_opcode:02X} result=0x{result_code:02X}")

            # Interpret successful responses as authoritative state confirmations
            # result_code 0x01 means success per FTMS
            try:
                if result_code == 0x01:
                    # START/RESUME (0x07) -> ACTIVE
                    if req_opcode == 0x07:
                        self._log("ControlPoint: START confirmed -> ACTIVE")
                        self._set_status(BikeStatus.ACTIVE)
                    # PAUSE/STOP (0x08) -> need to inspect last sent control param
                    elif req_opcode == 0x08:
                        param = None
                        if self._last_control_sent and len(self._last_control_sent) >= 2:
                            param = self._last_control_sent[1]
                        if param == 0x02:
                            self._log("ControlPoint: PAUSE confirmed -> PAUSED")
                            self._set_status(BikeStatus.PAUSED)
                        else:
                            self._log("ControlPoint: STOP confirmed -> READY")
                            self._set_status(BikeStatus.READY)
            except Exception as e:
                self._log(f"Failed to apply CP confirmation to status: {e}")

    def _on_ftm_status(self, sender: Any, data: bytearray):
        """Handle FTM Status characteristic notifications (authoritative state)."""
        try:
            payload = bytes(data)
            if not payload:
                return
            code = payload[0]
            # Map common server status opcodes to BikeStatus
            if code == 0x04:
                new_status = BikeStatus.ACTIVE
            elif code == 0x02:
                new_status = BikeStatus.PAUSED
            elif code in (0x01, 0x00):
                new_status = BikeStatus.READY
            else:
                return
            self._log(f"FTM Status notify code=0x{code:02X} -> {new_status.name}")
            self._set_status(new_status)
        except Exception as e:
            self._log(f"FTM Status parse failed: {e}")

    async def _ensure_control(self) -> bool:
        if not self.running:
            return False
        if self._control_requested:
            return True
        await self._tx_queue.put(bytes([0x00]))  # Request Control
        self._control_requested = True
        return True

    async def _tx_worker(self):
        self._log("TX worker started")
        while self.running:
            try:
                payload = await asyncio.wait_for(self._tx_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                if self.client and self.client.is_connected:
                    # record last control payload at time of write so indications can be interpreted
                    try:
                        self._last_control_sent = bytes(payload)
                    except Exception:
                        self._last_control_sent = None
                    await self.client.write_gatt_char(self.FTM_CONTROL_POINT_UUID, payload, response=True)
                else:
                    self._log("TX dropped: bike not connected")
            except Exception as e:
                self._log(f"Control write failed: {e}")
            finally:
                self._tx_queue.task_done()

            await asyncio.sleep(self.CONTROL_SEND_INTERVAL)

        self._log("TX worker stopped")

    async def _connect_internal(self) -> bool:
        if self.client and self.client.is_connected:
            return True

        self._connect_generation += 1
        generation = self._connect_generation
        self._log(f"Connecting to {self.mac_address} (gen={generation})...")

        try:
            client = BleakClient(self.mac_address, timeout=15.0, disconnected_callback=self._on_disconnected)
            await client.connect()
            if not client.is_connected:
                return False

            await client.start_notify(self.INDOOR_BIKE_DATA_UUID, self._on_indoor_bike_data)

            try:
                await client.start_notify(self.FTM_CONTROL_POINT_UUID, self._on_control_point_indication)
            except Exception as e:
                self._log(f"Control Point indication unavailable: {e}")
            try:
                await client.start_notify(self.FTM_STATUS_UUID, self._on_ftm_status)
            except Exception as e:
                self._log(f"FTM Status notify unavailable: {e}")

            self.client = client
            self._last_data_time = time.time()
            self._control_requested = False
            await self._ensure_control()
            self._log("Connected at BLE layer")
            return True
        except Exception as e:
            self._log(f"Connect internal error: {e}")
            await self._disconnect_internal()
            await asyncio.sleep(1.0)
            return False

    async def _disconnect_internal(self):
        if self.client:
            try:
                await self.client.stop_notify(self.INDOOR_BIKE_DATA_UUID)
            except Exception:
                pass
            try:
                await self.client.stop_notify(self.FTM_CONTROL_POINT_UUID)
            except Exception:
                pass
            try:
                await self.client.stop_notify(self.FTM_STATUS_UUID)
            except Exception:
                pass
            try:
                await self.client.disconnect()
            except Exception as e:
                self._log(f"Disconnect error ignored: {e}")
            finally:
                self.client = None

        self._control_requested = False

    async def _connection_manager(self):
        self._log("Connection manager started")
        had_successful_connection = False
        while self.running:
            ok = await self._connect_internal()
            if not ok:
                await asyncio.sleep(self.RECONNECT_INTERVAL_SEC)
                continue

            had_successful_connection = True
            while self.running and self.client and self.client.is_connected:
                await asyncio.sleep(1.0)

            if not self.running:
                break

            self._log("Connection lost, start reconnection")
            self._set_status(BikeStatus.READY)
            if had_successful_connection:
                self.data_callback(BikeData(raw_data="RECONNECTING"))
            await self._disconnect_internal()
            await asyncio.sleep(self.RECONNECT_INTERVAL_SEC)

        self._log("Connection manager stopped")

    async def _watchdog_loop(self):
        self._log("Watchdog started")
        while self.running:
            await asyncio.sleep(2.0)
            if not self.running:
                break
            if not self.client or not self.client.is_connected:
                continue

            idle_sec = time.time() - self._last_data_time
            if idle_sec > self.DATA_TIMEOUT_LIMIT:
                self._log(f"Data timeout detected ({idle_sec:.1f}s), forcing reconnect")
                await self._disconnect_internal()

        self._log("Watchdog stopped")

    async def _queue_control(self, payload: bytes) -> bool:
        if not self.running:
            self._log("Command ignored: driver is not running")
            return False
        await self._tx_queue.put(payload)
        return True

    def _map_level_to_ftms(self, level: int) -> int:
        # 0.1 precision representation, 1..24 => 10..240
        return max(0, min(255, int(round(level * 10))))

    async def set_resistance(self, level: int):
        if level < 1 or level > 24:
            self._log(f"Resistance must be 1-24, got {level}")
            return False

        if hasattr(self, "_current_resistance") and level == self._current_resistance:
            self._log(f"Resistance already {level}, skip")
            return True

        if not await self._ensure_control():
            return False
        ftms_value = self._map_level_to_ftms(level)
        ok = await self._queue_control(bytes([0x04, ftms_value]))
        if ok:
            self._current_resistance = level
            self._log(f"Set resistance level={level} (ftms={ftms_value})")
        return ok

    async def stop_bike(self):
        if not await self._ensure_control():
            return False
        ok = await self._queue_control(bytes([0x08, 0x01]))
        if ok:
            self._set_status(BikeStatus.READY)
            self._log("Sent stop command")
        return ok

    async def pause_bike(self):
        if not await self._ensure_control():
            return False
        ok = await self._queue_control(bytes([0x08, 0x02]))
        if ok:
            self._set_status(BikeStatus.PAUSED)
            self._log("Sent pause command")
        return ok

    async def start_bike(self):
        if not await self._ensure_control():
            return False
        ok = await self._queue_control(bytes([0x07]))
        if ok:
            # Interface preserved for compatibility with BikeService,
            # but do not change status here — wait for explicit confirmation.
            self._log("Sent start/resume command (status change deferred)")
        return ok

    async def wake_bike(self):
        ok = await self._queue_control(bytes([0x00]))
        if ok:
            self._log("Sent request control (wake)")
        return ok

    def get_current_data(self) -> dict:
        return {
            "duration": self._duration,
            "distance": self._distance,
            "power": self._power,
            "cadence": self._rpm,
            "resistance": self._current_resistance,
            "calories": self._calories,
            "status": self._status,
            "speed": self._speed,
        }

    async def start(self):
        if self.running:
            return
        self._log("Starting FTMS bike driver...")
        self.running = True
        self._last_data_time = time.time()

        self._tx_worker_task = asyncio.create_task(self._tx_worker())
        self._manager_task = asyncio.create_task(self._connection_manager())
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self):
        self._log("Stopping FTMS bike driver...")
        self.running = False

        for task in (self._watchdog_task, self._manager_task, self._tx_worker_task):
            if task:
                task.cancel()

        self._watchdog_task = None
        self._manager_task = None
        self._tx_worker_task = None

        await self._disconnect_internal()

        while not self._tx_queue.empty():
            try:
                self._tx_queue.get_nowait()
                self._tx_queue.task_done()
            except Exception:
                break

        self._log("Driver completely stopped")
