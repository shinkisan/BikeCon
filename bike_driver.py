import asyncio
import time
import struct
import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any
from pathlib import Path
from bleak import BleakClient
from bleak.exc import BleakError

IDENTITY_PATH = Path("/etc/BikeCon/identity.json")

def to_int(val):
    if isinstance(val, (bytes, bytearray)):
        return int.from_bytes(val, 'little') if len(val) <= 4 else 0
    return int(val) if isinstance(val, (int, float)) else 0

class BikeStatus(Enum):
    UNKNOWN = 0
    READY = 1     # 待机/随时可骑 (实测 STA 01)
    TRANSITION = 2      # 倒计时或退出阶段 (实测 STA 02)
    ACTIVE = 3    # 正在骑行 (实测 STA 03)
    PAUSED = 4    # 手动暂停 (实测 STA 04)

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
    CHAR_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
    
    # 字段 ID (Field Number) 
    FIELD_DISTANCE = 2
    FIELD_DURATION = 3
    FIELD_CALORIES = 4
    FIELD_RESISTANCE = 5
    FIELD_RPM = 6
    FIELD_POWER = 7
    FIELD_STATUS = 8
    
    # Protocol 常量
    SRC_ID_PHONE = bytes.fromhex("3216ef23")
    DST_SUB_PHONE = bytes.fromhex("5501")
    MSG_TYPE = bytes.fromhex("93")
    SESSION_IDLE = bytes.fromhex("0000")
    SESSION_ACTIVE = bytes.fromhex("0400")
    FIXED_BYTE = bytes.fromhex("01")
    CMD_2F37 = b'\xb5\x31\x30\x36\x2f\x37'
    CMD_2F34 = b'\xb5\x31\x30\x36\x2f\x34'
    FRAME_MAGIC = b'\xA5\xA5\xA0'

    HEARTBEAT_INTERVAL = 1.0
    DATA_TIMEOUT_LIMIT = 20.0
    RECONNECT_INTERVAL_SEC = 5.0

    def __init__(self, 
                 mac_address: str, 
                 data_callback: Callable[[BikeData], None],
                 status_callback: Optional[Callable[[BikeStatus, BikeStatus], None]] = None):
        self.mac_address = mac_address
        self.data_callback = data_callback
        self.status_callback = status_callback
        
        self.client: Optional[BleakClient] = None
        self.running = False  
        self._seq = 0
        self._app_counter = 0xA4 
        self._current_status = BikeStatus.READY
        self._last_data_time = time.time()
        self._last_calories = 0
        self._prev_dist = None
        self._prev_dur = None
        self.reconnect_interval_sec = self.RECONNECT_INTERVAL_SEC
        
        # 任务控制
        self._watchdog_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._tx_worker_task: Optional[asyncio.Task] = None
        self._tx_queue = asyncio.Queue()
        
        # 身份验证
        self.handshake_packets = []
        self._load_identity()

    def _log(self, msg: str, level: str = None):
        """统一日志打印"""
        print(f"[BikeDriver] {msg}")

    def _load_identity(self):
        try:
            if IDENTITY_PATH.exists():
                identity = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
                self.handshake_packets = identity.get("handshake_packets", [])
            else:
                raise ValueError("identity.json not found")
        except Exception as e:
            raise ValueError(f"Failed to load identity.json: {e}")
        
        if not self.handshake_packets:
            raise ValueError("CRITICAL: handshake_packets is required in identity.json")
        self._log("handshake_packets loaded")

    # ================= 协议打包与解析层 =================
    def _decode_protobuf(self, pb_data: bytes) -> dict[int, Any]:
        """通用的 Protobuf 动态扫描引擎"""
        results = {}
        ptr = 0
        while ptr < len(pb_data):
            try:
                tag = pb_data[ptr]
                field_num = tag >> 3     # 提取字段 ID
                wire_type = tag & 0x07   # 提取数据类型
                ptr += 1
                
                if wire_type == 0:       # Varint (整数)
                    val, consumed = self._read_varint(pb_data, ptr)
                    results[field_num] = val
                    ptr += consumed
                elif wire_type == 2:     # Length-delimited (字符串或字节数组)
                    length, consumed = self._read_varint(pb_data, ptr)
                    ptr += consumed
                    results[field_num] = pb_data[ptr : ptr + length]
                    ptr += length
                else:
                    ptr += 1             # 跳过未知类型
            except Exception:
                break
        return results

    async def _send_handshake(self):
        if not self.handshake_packets:
            raise ValueError("No handshake_packets found in identity.json")
        
        self._log(f"Sending handshake...")
        for i, pkt_hex in enumerate(self.handshake_packets):
            pkt = bytes.fromhex(pkt_hex) if isinstance(pkt_hex, str) else bytes(pkt_hex)
            try:
                await self.client.write_gatt_char(self.CHAR_UUID, pkt, response=False)
                self._log(f"Handshake pkt {i+1}/{len(self.handshake_packets)} sent")
                await asyncio.sleep(0.05)
            except Exception as e:
                self._log(f"Failed to send handshake pkt {i+1}: {e}")
        self._log("Handshake complete.")

    async def _send_command(self, cmd_str: bytes, session=None, field_cnt=0, extra=b'', is_handshake=False, dst_sub=None):
        """立即构建并向底层写入一帧数据 [cite: 13]"""
        if not self.client or not self.client.is_connected:
            return
            
        if session is None: session = self.SESSION_IDLE
        # 如果没传 dst_sub，则默认使用原有的 5501 
        if dst_sub is None: dst_sub = self.DST_SUB_PHONE
        
        # 构建 Payload [cite: 21]
        cnt = self._app_counter
        self._app_counter = (self._app_counter + 257) & 0xFFFF # 应用层计数器每包递增 257 [cite: 22, 99]
        app_cnt = struct.pack("<H", cnt)
        
        payload = (
            self.SRC_ID_PHONE + dst_sub + self.MSG_TYPE + app_cnt + 
            session + bytes([field_cnt]) + self.FIXED_BYTE + cmd_str
        )
        if extra: payload += b'\xFF' + extra # 附加字段前缀 
            
        # 构建帧并计算 CRC16 [cite: 13, 19]
        body = self.FRAME_MAGIC + struct.pack("B", self._seq) + struct.pack("<H", len(payload)) + payload
        crc = self._crc16(body)
        self._seq = (self._seq + 1) % 256
        pkt = body + struct.pack("<H", crc)
        
        if is_handshake:
            self._log(f"[Handshake Debug] TX -> {pkt.hex()}")
        
        try:
            await self.client.write_gatt_char(self.CHAR_UUID, pkt, response=False)
        except Exception as e:
            self._log(f"Write failed: {e}")

    def _read_varint(self, data: bytes, start_idx: int):
        """【修复】增加 varint 读取边界保护，防脏数据死循环"""
        res = 0
        shift = 0
        count = 0
        for i in range(start_idx, len(data)):
            byte = data[i]
            res |= (byte & 0x7F) << shift
            count += 1
            if not (byte & 0x80) or count >= 10:  # 最大读取 10 字节 (64位)
                break
            shift += 7
        return res, count

    def _write_varint(self, value: int) -> bytes:
        result = []
        while value >= 0x80:
            result.append((value & 0x7F) | 0x80)
            value >>= 7
        result.append(value)
        return bytes(result)

    def _crc16(self, data: bytes) -> int:
        crc = 0x0000
        for byte in data:
            crc ^= (byte << 8)
            for _ in range(8):
                if crc & 0x8000: crc = (crc << 1) ^ 0x1021
                else: crc <<= 1
                crc &= 0xFFFF
        return crc

    async def set_resistance(self, level: int):
        """设置阻力等级 (1-24)"""
        if level < 1 or level > 24:
            self._log(f"Resistance must be 1-24, got {level}")
            return False

        if hasattr(self, '_current_resistance') and level == self._current_resistance:
            self._log(f"Resistance already {level}, skip")
            return True

        self._resistance_cnt = getattr(self, '_resistance_cnt', 0x06) + 1
        cnt = self._resistance_cnt

        payload = (
            bytes.fromhex("3216ef235503")
            + bytes([0xb0, cnt & 0xFF, (cnt + 9) & 0xFF])
            + bytes.fromhex("04000002b53130362f36ff08")
            + bytes([level])
        )

        packet = self._build_control_packet(payload)
        await self._smart_write(packet)
        
        self._current_resistance = level
        self._log(f"Set resistance: {level}")
        return True

    async def stop_bike(self):
        """发送停止指令"""
        self._resistance_cnt = getattr(self, '_resistance_cnt', 0x06) + 1
        cnt = self._resistance_cnt

        payload = (
            bytes.fromhex("3216ef235503")
            + bytes([0xb0, cnt & 0xFF, (cnt + 9) & 0xFF])
            + bytes.fromhex("04000002b53130362f34ff0801")
        )

        packet = self._build_control_packet(payload)
        await self._smart_write(packet)
        
        self._log("Sent stop command")
        return True
    
    async def pause_bike(self):
        """发送暂停指令"""
        self._resistance_cnt = getattr(self, '_resistance_cnt', 0x06) + 1
        cnt = self._resistance_cnt

        payload = (
            bytes.fromhex("3216ef235503")
            + bytes([0xb0, cnt & 0xFF, (cnt + 9) & 0xFF])
            + bytes.fromhex("04000002b53130362f34ff0804")
        )

        packet = self._build_control_packet(payload)
        await self._smart_write(packet)
        
        self._log("Sent pause command")
        return True

    async def start_bike(self):
        """发送开始/恢复指令"""
        self._resistance_cnt = getattr(self, '_resistance_cnt', 0x06) + 1
        cnt = self._resistance_cnt

        payload = (
            bytes.fromhex("3216ef235503")
            + bytes([0xb0, cnt & 0xFF, (cnt + 9) & 0xFF])
            + bytes.fromhex("04000002b53130362f34ff0803")
        )

        packet = self._build_control_packet(payload)
        await self._smart_write(packet)
        
        self._log("Sent start command")
        return True
    
    async def wake_bike(self):
        """发送唤醒指令"""
        self._resistance_cnt = getattr(self, '_resistance_cnt', 0x06) + 1
        cnt = self._resistance_cnt

        payload = (
            bytes.fromhex("3216ef235503")
            + bytes([0xb0, cnt & 0xFF, (cnt + 9) & 0xFF])
            + bytes.fromhex("04000002b53130362f34ff0802")
        )

        packet = self._build_control_packet(payload)
        await self._smart_write(packet)
        
        self._log("Sent wake command")
        return True

    def get_current_data(self) -> dict:
        """获取当前单车数据"""
        return {
            "duration": getattr(self, '_duration', 0),
            "distance": getattr(self, '_distance', 0),
            "power": getattr(self, '_power', 0),
            "cadence": getattr(self, '_rpm', 0),
            "resistance": getattr(self, '_current_resistance', 1),
            "calories": getattr(self, '_calories', 0),
            "status": getattr(self, '_status', 2),
            "speed": getattr(self, '_speed', 0.0),
        }

    def _build_control_packet(self, payload: bytes) -> bytes:
        """构建控制指令数据包"""
        header = bytearray([0xA5, 0xA5, 0xA0, self._seq])
        header += struct.pack("<H", len(payload))
        packet = header + payload
        packet += struct.pack("<H", self._crc16(packet))
        self._seq = (self._seq + 1) & 0xFF
        return bytes(packet)

    async def _smart_write(self, data: bytes):
        """智能写入数据"""
        if not self.client or not self.client.is_connected:
            return
        try:
            await self.client.write_gatt_char(self.CHAR_UUID, data, response=False)
        except Exception as e:
            self._log(f"Write failed: {e}")

    # ================= 业务与数据处理层 =================

    def _notification_handler(self, sender: Any, data: bytearray):
        self._last_data_time = time.time()

        # 1. 定位数据正文
        marker = self.CMD_2F37 + b'\xff'
        marker_idx = data.find(marker)
        
        if marker_idx == -1:
            return

        # 2. 获取有效正文并解析
        start_ptr = marker_idx + len(marker)
        pb_data = data[start_ptr : -2]
        fields = self._decode_protobuf(pb_data)

        # 3. 提取基础数据
        res = {
            'rpm': to_int(fields.get(self.FIELD_RPM, 0)),
            'power': to_int(fields.get(self.FIELD_POWER, 0)),
            'duration': to_int(fields.get(self.FIELD_DURATION, 0)),
            'distance': to_int(fields.get(self.FIELD_DISTANCE, 0)),
            'resistance': to_int(fields.get(self.FIELD_RESISTANCE, 1)),
            'calories': to_int(fields.get(self.FIELD_CALORIES, 0)) / 1.0,
            'status_code': to_int(fields.get(self.FIELD_STATUS, 2))
        }

        # 4. 智能速度计算
        current_speed = 0.0
        if self._prev_dist is not None and self._prev_dur is not None:
            delta_d = res['distance'] - self._prev_dist
            delta_t = res['duration'] - self._prev_dur
            if delta_t > 0 and delta_d >= 0:
                current_speed = (delta_d / delta_t) * 3.6
        
        self._prev_dist = res['distance']
        self._prev_dur = res['duration']

       # 5. 状态感知与数据清洗 (优化后)
        status_code = res['status_code']
        try:
            new_status = BikeStatus(status_code)
        except ValueError:
            new_status = BikeStatus.UNKNOWN

        # 核心逻辑：只有在真正的 ACTIVE 状态下才保留转速和功率
        # 在 READY (01), TRANSITION (02), PAUSED (04) 时，全部强制归零
        if new_status != BikeStatus.ACTIVE:
            res['rpm'] = 0
            res['power'] = 0
            current_speed = 0.0

        # 6. 更新状态回调
        if new_status != self._current_status:
            if self.status_callback:
                self.status_callback(self._current_status, new_status)
            self._current_status = new_status

        # 7. 存储数据供 get_current_data() 使用
        self._rpm = res['rpm']
        self._power = res['power']
        self._duration = res['duration']
        self._distance = res['distance']
        self._calories = res['calories']
        self._status = res['status_code']
        self._speed = round(current_speed, 1)
        if not hasattr(self, '_current_resistance'):
            self._current_resistance = res['resistance']

        # 8. 组装并推送数据
        bike_data = BikeData(**res, speed=round(current_speed, 1), raw_data=data.hex())
        self._last_calories = int(res['calories'])
        self.data_callback(bike_data)

    # ================= 核心并发任务控制层 =================

    async def _tx_worker(self):
        self._log("TX Worker started.")
        while self.running:
            try:
                msg_type, payload = await asyncio.wait_for(self._tx_queue.get(), timeout=1.0)
                if msg_type == "ACK":
                    await self._send_command(self.CMD_2F34, extra=b'\x01')
                    self._log("Session end ACK sent.")
                self._tx_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self._log(f"TX Worker error: {e}", "ERROR")
        self._log("TX Worker stopped.")

    async def _heartbeat_loop(self):
        self._log("Heartbeat Loop started.")
        while self.running and self.client and self.client.is_connected:
            try:
                # 只有不是 ACTIVE 状态（READY/TRANSITION/PAUSED），才发送不带卡路里的心跳
                if self._current_status != BikeStatus.ACTIVE: 
                    await self._send_command(self.CMD_2F37, session=self.SESSION_ACTIVE)
                else:
                    varint_cal = self._write_varint(self._last_calories * 1000)
                    await self._send_command(self.CMD_2F37, session=self.SESSION_ACTIVE, field_cnt=0, extra=b'\x20' + varint_cal)
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            except Exception as e:
                self._log(f"Heartbeat send failed: {e}", "WARN")
                break
        self._log("Heartbeat Loop stopped.")

    async def _watchdog_loop(self):
        self._log("Watchdog started.")
        while self.running:
            await asyncio.sleep(2.0)
            if not self.running: break
            
            is_disconnected = not self.client or not self.client.is_connected
            is_timeout = (time.time() - self._last_data_time) > self.DATA_TIMEOUT_LIMIT
            
            if is_disconnected or is_timeout:
                reason = "Disconnected" if is_disconnected else f"Timeout ({time.time() - self._last_data_time:.1f}s)"
                self._log(f"Connection lost detected. Reason: {reason}", "WARN")
                
                # 通知 UI 掉线重连
                if self._current_status != BikeStatus.READY:
                    old_status = self._current_status
                    self._current_status = BikeStatus.READY
                    if self.status_callback: 
                        self.status_callback(old_status, BikeStatus.READY)
                self.data_callback(BikeData(raw_data="RECONNECTING"))
                
                # 清理旧连接
                await self._disconnect_internal()
                
                # 重连循环
                self._log("Entering reconnection loop...")
                while self.running:
                    if await self._connect_internal():
                        self._log("Reconnected successfully! 🎉")
                        break
                    
                    self._log(f"Reconnect failed. Waiting {int(self.reconnect_interval_sec)}s before next attempt...")
                    for _ in range(int(self.reconnect_interval_sec)):
                        if not self.running: break
                        await asyncio.sleep(1.0)
                        
        self._log("Watchdog stopped.")

    # ================= 暴露的生命周期 API =================

    async def start(self):
        """统一的启动入口，只需调用一次"""
        if self.running: return
        self._log("Starting Bike Driver...")
        self.running = True
        self._last_data_time = time.time()
        
        # 仅启动看门狗，让看门狗去负责建立第一通连接
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self):
        """统一的停止入口，安全退出所有协程和连接"""
        self._log("Stopping Bike Driver...")
        self.running = False
        
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
            
        await self._disconnect_internal()
        self._log("Driver completely stopped.")

    async def _connect_internal(self) -> bool:
        """内部的连接过程封装，仅负责建立链路和初始化通道"""
        if self.client and self.client.is_connected: return True
        
        self._log(f"Connecting to {self.mac_address}...")
        try:
            self.client = BleakClient(self.mac_address, timeout=15.0)
            await self.client.connect()
            
            if self.client.is_connected:
                self._log("Connected at BLE layer.")
                self._last_data_time = time.time()

                self._app_counter = 0xA400  # 对齐重放包起始值 
                self._seq = 0               # 传输层 Seq 也要归零 [cite: 16]
                
                await self.client.start_notify(self.CHAR_UUID, self._notification_handler)
                
                # 发送动态握手包
                await self._send_handshake()
                self._last_data_time = time.time()
                
                # 启动辅助任务
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                self._tx_worker_task = asyncio.create_task(self._tx_worker())
                
                return True
        except Exception as e:
            self._log(f"Connect internal error: {e}", "ERROR")
            await self._disconnect_internal()
            await asyncio.sleep(1.0) # 让 BlueZ 缓一缓
            
        return False

    async def _disconnect_internal(self):
        """内部的资源清理过程封装"""
        self._log("Cleaning up connection resources...")
        
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
            
        if self._tx_worker_task:
            self._tx_worker_task.cancel()
            self._tx_worker_task = None
            
        # 清空发送队列
        while not self._tx_queue.empty():
            self._tx_queue.get_nowait()
            self._tx_queue.task_done()

        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                self._log(f"Disconnect error ignored: {e}", "WARN")
            finally:
                self.client = None
