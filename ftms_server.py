import asyncio
import logging
import signal
import struct
import json
from typing import Any, Optional
from dataclasses import dataclass
from enum import Enum

from bless import (
    BlessServer,
    BlessGATTCharacteristic,
    GATTCharacteristicProperties,
    GATTAttributePermissions,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FTMSOpCode(Enum):
    REQUEST_CONTROL = 0x00
    RESET = 0x01
    SET_TARGET_RESISTANCE = 0x04
    SET_TARGET_POWER = 0x06
    START_RESUME = 0x07
    PAUSE_STOP = 0x08
    SET_INDOOR_BIKE_SIMULATION = 0x11


FTMS_UUID = "00001826-0000-1000-8000-00805f9b34fb"
FTM_FEATURE_UUID = "00002acc-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA_UUID = "00002ad2-0000-1000-8000-00805f9b34fb"
FTM_CONTROL_POINT_UUID = "00002ad9-0000-1000-8000-00805f9b34fb"
FTM_STATUS_UUID = "00002ada-0000-1000-8000-00805f9b34fb"
SUPPORTED_RESISTANCE_LEVEL_RANGE_UUID = "00002ad6-0000-1000-8000-00805f9b34fb"

DEVICE_INFO_SERVICE = "0000180A-0000-1000-8000-00805f9b34fb"
CHAR_MANUFACTURER_NAME = "00002A29-0000-1000-8000-00805f9b34fb"
CHAR_MODEL_NUMBER = "00002A24-0000-1000-8000-00805f9b34fb"

PUBSUB_SOCKET = "/var/run/BikeCon/pubsub.sock"
CONTROL_SOCKET = "/var/run/BikeCon/control.sock"


@dataclass
class BikeData:
    rpm: int = 0
    power: int = 0
    speed: float = 0.0
    distance: int = 0
    calories: int = 0
    resistance: float = 10.0
    status: int = 1
    duration: int = 0


class FTMSGattServer:
    def __init__(self, name: str = "BikeCon-FTMS"):
        self.name = name
        self.server: Optional[BlessServer] = None
        self.running = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._current_resistance: float = 10.0
        self._current_status: int = 1
        self._is_started: bool = False
        
        self._pubsub_reader = None
        self._pubsub_writer = None
        self._control_writer = None
        self._current_data = BikeData()

    def _log(self, msg: str, level: str = "INFO"):
        print(f"[FTMS] {msg}")

    def _parse_control_point(self, data: bytearray) -> dict:
        if len(data) < 1:
            return {"opcode": None, "raw": data.hex()}

        opcode = data[0]
        result = {
            "opcode": opcode,
            "command": FTMSOpCode(opcode).name if opcode in [e.value for e in FTMSOpCode] else f"UNKNOWN_0x{opcode:02X}"
        }

        try:
            if opcode == 0x04 and len(data) >= 2:
                ftms_resistance = data[1]
                result["ftms_resistance"] = ftms_resistance
                result["resistance_percent"] = (ftms_resistance / 255.0) * 100
            elif opcode == 0x08 and len(data) >= 2:
                result["parameter"] = data[1]
                if data[1] == 0x01:
                    result["command"] = "STOP"
                elif data[1] == 0x02:
                    result["command"] = "PAUSE"
            elif opcode == 0x06 and len(data) >= 3:
                target_power = struct.unpack("<h", data[1:3])[0]
                result["target_power"] = target_power
            elif opcode == 0x11 and len(data) >= 7:
                wind_raw, grade_raw, crr_raw, cw_raw = struct.unpack('<hhBB', data[1:7])
                result["wind_speed"] = wind_raw * 0.01
                result["grade"] = grade_raw * 0.01
                result["crr"] = crr_raw * 0.0001
        except Exception as e:
            result["parse_error"] = str(e)

        result["raw"] = data.hex()
        return result

    def on_control_point_write(self, characteristic: BlessGATTCharacteristic, value: Any, **kwargs):
        data = bytearray(value) if isinstance(value, bytes) else bytearray(value)
        parsed = self._parse_control_point(data)
        
        self._log(f"收到 Control Point 写入: {parsed}")
        
        op_code = parsed["opcode"]
        param = parsed.get("parameter", 0)
        response_code = 0x01
        
        if op_code == FTMSOpCode.SET_TARGET_RESISTANCE.value:
            ftms_value = data[1] # 取得原始字节
            hw_level = self._ftms_to_hardware_level(ftms_value)
            self._current_resistance = ftms_value / 10.0
            self._log(f"→ 调阻指令: 协议等级 {self._current_resistance} -> 硬件档位 {hw_level}")
            asyncio.create_task(self._send_control({"type": "set_resistance", "level": hw_level}))
        elif op_code == 0x08:
            if param == 0x01:
                self._current_status = 1
                self._is_started = False
                self._log("→ 停止骑行")
                asyncio.create_task(self._send_control({"type": "stop"}))
            elif param == 0x02:
                self._current_status = 4
                self._is_started = False
                self._log("→ 暂停骑行")
                asyncio.create_task(self._send_control({"type": "pause"}))
            else:
                self._log(f"→ 未知参数: {param}")
                response_code = 0x02
        elif op_code == FTMSOpCode.START_RESUME.value:
            self._current_status = 2
            self._is_started = True
            self._log("→ 开始/恢复骑行")
            asyncio.create_task(self._send_control({"type": "start"}))
        elif op_code == FTMSOpCode.REQUEST_CONTROL.value:
            self._log("→ 请求控制，唤醒单车")
            asyncio.create_task(self._send_control({"type": "wake"}))
        elif op_code == FTMSOpCode.RESET.value:
            self._log("→ 重置")
        elif op_code == FTMSOpCode.SET_INDOOR_BIKE_SIMULATION.value:
            self._log(f"→ 室内单车模拟: {parsed}")
            grade = parsed.get("grade", 0)
            hw_level = self._calculate_resistance(grade)
            asyncio.create_task(self._send_control({"type": "set_resistance", "level": hw_level}))
        else:
            response_code = 0x02

        response = bytearray([0x80, op_code, response_code])
        try:
            self.server.get_characteristic(FTM_CONTROL_POINT_UUID).value = response
            self.server.update_value(FTMS_UUID, FTM_CONTROL_POINT_UUID)
        except Exception as e:
            self._log(f"发送响应失败: {e}", "ERROR")

    def _calculate_resistance(self, grade_pct: float) -> int:
        """将坡度转换为阻力等级 (1-24)"""
        base_level = 6
        grade_contribution = grade_pct * 1.5
        level = round(base_level + grade_contribution)
        return max(1, min(24, level))

    def _ftms_to_hardware_level(self, ftms_value: int) -> int:
        """符合规范的转换：FTMS 原始值 0-255 代表等级 0.0-25.5"""
        # 假设你的 Keep 单车有 24 档
        # 我们将 FTMS 的 0.0-24.0 映射到硬件的 1-24 档
        actual_level = ftms_value / 10.0  # 转换为协议定义的实际等级
        hw_level = round(actual_level)
        return max(1, min(24, hw_level))

    def on_read(self, characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
        return characteristic.value

    async def _setup_services(self):
        await self.server.add_new_service(FTMS_UUID)

        feature_val = 0x00005307
        target_val = 0x0000000C
        await self.server.add_new_characteristic(
            FTMS_UUID, FTM_FEATURE_UUID,
            GATTCharacteristicProperties.read, 
            struct.pack('<I', feature_val) + struct.pack('<I', target_val), 
            GATTAttributePermissions.readable
        )

        await self.server.add_new_characteristic(
            FTMS_UUID, INDOOR_BIKE_DATA_UUID,
            GATTCharacteristicProperties.notify, 
            bytearray([0]*20), 
            GATTAttributePermissions.readable
        )

        await self.server.add_new_characteristic(
            FTMS_UUID, FTM_STATUS_UUID,
            GATTCharacteristicProperties.notify, 
            bytearray([0x00]), 
            GATTAttributePermissions.readable
        )

        await self.server.add_new_characteristic(
            FTMS_UUID, FTM_CONTROL_POINT_UUID,
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.indicate,
            bytearray([0x00]),
            GATTAttributePermissions.writeable
        )

        await self.server.add_new_characteristic(
            FTMS_UUID, SUPPORTED_RESISTANCE_LEVEL_RANGE_UUID,
            GATTCharacteristicProperties.read, 
            struct.pack('<hhH', 0, 1000, 43), 
            GATTAttributePermissions.readable
        )

        await self.server.add_new_service(DEVICE_INFO_SERVICE)
        await self.server.add_new_characteristic(
            DEVICE_INFO_SERVICE, CHAR_MANUFACTURER_NAME,
            GATTCharacteristicProperties.read, b"BikeCon",
            GATTAttributePermissions.readable
        )
        await self.server.add_new_characteristic(
            DEVICE_INFO_SERVICE, CHAR_MODEL_NUMBER,
            GATTCharacteristicProperties.read, b"BikeCon-FTMS",
            GATTAttributePermissions.readable
        )

        self._log("FTMS 服务注册完成")

    async def start(self):
        self.loop = asyncio.get_running_loop()
        
        await self._connect_pubsub()
        await self._connect_control()
        
        self.server = BlessServer(name=self.name, loop=self.loop)
        self.server.read_request_func = self.on_read
        self.server.write_request_func = self.on_control_point_write

        await self._setup_services()
        
        await self.server.start()
        self.running = True
        self._log(f"FTMS Server 已启动: {self.name}")
        
        asyncio.create_task(self._receive_bike_data())
        asyncio.create_task(self._broadcast_loop())

    async def _connect_pubsub(self):
        try:
            self._pubsub_reader, self._pubsub_writer = await asyncio.open_unix_connection(PUBSUB_SOCKET)
            self._log(f"Connected to pubsub: {PUBSUB_SOCKET}")
        except Exception as e:
            self._log(f"Failed to connect to pubsub: {e}", "ERROR")

    async def _connect_control(self):
        try:
            _, self._control_writer = await asyncio.open_unix_connection(CONTROL_SOCKET)
            self._log(f"Connected to control: {CONTROL_SOCKET}")
        except Exception as e:
            self._log(f"Failed to connect to control: {e}", "ERROR")

    async def _send_control(self, msg: dict):
        if self._control_writer and not self._control_writer.is_closing():
            try:
                self._control_writer.write(json.dumps(msg).encode() + b'\n')
                await self._control_writer.drain()
            except Exception as e:
                self._log(f"Send control failed: {e}", "ERROR")

    async def _receive_bike_data(self):
        while self.running:
            try:
                if self._pubsub_reader:
                    line = await self._pubsub_reader.readline()
                    if line:
                        try:
                            data = json.loads(line.decode().strip())
                            self._update_bike_data(data)
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                self._log(f"Receive error: {e}", "ERROR")
                await asyncio.sleep(1)
                await self._connect_pubsub()
            await asyncio.sleep(0.1)

    def _update_bike_data(self, data: dict):
        self._current_data.rpm = data.get("rpm", 0)
        self._current_data.power = data.get("power", 0)
        self._current_data.speed = data.get("speed", 0.0)
        self._current_data.distance = data.get("distance", 0)
        self._current_data.calories = data.get("calories", 0)
        self._current_data.resistance = data.get("resistance", 10)
        self._current_data.status = data.get("status", 1)
        self._current_data.duration = data.get("duration", 0)

    async def _broadcast_loop(self):
        while self.running:
            await asyncio.sleep(1.0)
            if self._current_data.rpm > 0 or self._current_data.power > 0:
                self.notify_bike_data(self._current_data)

    async def stop(self):
        if self.server and self.running:
            try:
                await self.server.stop()
            except Exception as e:
                self._log(f"停止服务时出错: {e}", "WARN")
        
        if self._pubsub_writer:
            self._pubsub_writer.close()
        if self._control_writer:
            self._control_writer.close()
            
        self.running = False
        self._log("FTMS Server 已停止")

    def notify_bike_data(self, data: BikeData):
        if not self.server or not self.running:
            return

        payload = self._build_indoor_bike_data(data)
        try:
            self.server.get_characteristic(INDOOR_BIKE_DATA_UUID).value = payload
            self.server.update_value(FTMS_UUID, INDOOR_BIKE_DATA_UUID)
        except Exception as e:
            self._log(f"通知失败: {e}", "ERROR")

    def _build_indoor_bike_data(self, data: BikeData) -> bytearray:
        flags = 0x0974
        
        speed_raw = int(data.speed * 100)
        cadence_raw = int(data.rpm * 2)
        dist_24bit = data.distance & 0xFFFFFF
        res_raw = int(data.resistance)
        pwr_raw = int(data.power)
        kcal_total = min(int(data.calories), 0xFFFF)
        kcal_hr = 500
        kcal_min = 10
        time_sec = min(data.duration, 0xFFFF)
        
        payload = bytearray()
        payload += struct.pack('<H', flags)
        payload += struct.pack('<H', speed_raw)
        payload += struct.pack('<H', cadence_raw)
        payload += struct.pack('<I', dist_24bit)[:3]
        payload += struct.pack('<h', res_raw)
        payload += struct.pack('<h', pwr_raw)
        payload += struct.pack('<H', kcal_total)
        payload += struct.pack('<H', kcal_hr)
        payload += struct.pack('<B', kcal_min)
        payload += struct.pack('<H', time_sec)
        
        return payload


async def main():
    server = FTMSGattServer(name="BikeCon-FTMS")
    
    stop_event = asyncio.Event()
    
    def signal_handler():
        print("\n[FTMS] 收到终止信号")
        stop_event.set()
    
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        await server.start()
        print("[FTMS] 服务运行中，按 Ctrl+C 停止")
        await stop_event.wait()
    except Exception as e:
        print(f"[FTMS] 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if server.running:
            await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
