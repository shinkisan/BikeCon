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

class BikeStatus(Enum):
    IDLE = 0
    ACTIVE = 1

@dataclass
class BikeData:
    rpm: int = 0
    power: int = 0
    duration: int = 0 
    resistance: int = 0 
    calories: int = 0  
    status_code: int = 0 
    raw_data: Optional[str] = None

class BikeClient:
    CHAR_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
    MAGIC_KEY = bytes.fromhex("CB06")
    
    # Tag 定义
    TAG_RPM = 0x30
    TAG_POWER = 0x38
    TAG_STATUS = 0x10 
    TAG_DURATION = 0x18
    TAG_CALORIES = 0x20 
    TAG_RESISTANCE = 0x28
    
    # Constants from reverse report
    SRC_ID_PHONE = bytes.fromhex("3216ef23")
    DST_SUB_PHONE = bytes.fromhex("5501")
    MSG_TYPE = bytes.fromhex("93")
    SESSION_IDLE = bytes.fromhex("0000")
    SESSION_ACTIVE = bytes.fromhex("0400")
    FIXED_BYTE = bytes.fromhex("01")
    DST_SUB_SYNC = bytes.fromhex("5503")
    SESSION_SYNC = bytes.fromhex("0300")
    CMD_2F31 = b'\xb3\x31\x2f\x31'
    CMD_2F33 = b'\xb3\x30\x2f\x33'
    CMD_2F31_SHORT = b'\xb3\x30\x2f\x31'          # 对应 "0/1"  
    CMD_2F33_LONG  = b'\xb5\x31\x30\x36\x2f\x33'  # 对应 "106/3"
    CMD_2F37 = b'\xb5\x31\x30\x36\x2f\x37'
    CMD_2F34 = b'\xb5\x31\x30\x36\x2f\x34'
    FRAME_MAGIC = b'\xA5\xA5\xA0'
    HEARTBEAT_INTERVAL = 1.0
    DATA_TIMEOUT_LIMIT = 20.0

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
        self._current_status = BikeStatus.IDLE
        self._last_data_time = time.time()
        self._last_calories = 0
        
        # 任务控制
        self._watchdog_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._tx_worker_task: Optional[asyncio.Task] = None
        self._tx_queue = asyncio.Queue()
        
        # Identity data
        self.client_id = ""
        self.uuid1 = ""
        self.uuid2 = ""
        self._load_identity()

    def _log(self, msg: str, level: str = None):
        """统一日志打印"""
        print(f"[BikeDriver] {msg}")

    def _load_identity(self):
        try:
            if IDENTITY_PATH.exists():
                identity = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
                self.client_id = identity.get("client_id", "").replace(":", "").lower()
                self.uuid1 = identity.get("uuid1", "")
                self.uuid2 = identity.get("uuid2", "")
            else:
                raise ValueError("identity.json not found")
        except Exception as e:
            raise ValueError(f"Failed to load identity.json: {e}")
        
        if not all([self.client_id, self.uuid1, self.uuid2]):
            raise ValueError("CRITICAL: Missing required fields in identity.json")
        self._log("Identity loaded successfully.")

    # ================= 协议打包与解析层 =================

    async def _send_handshake(self):
        self._log("构建并发送动态握手包...")
        
        # Pkt1: 手机宣告 Client ID (16字节 ASCII) [cite: 32, 35]
        extra1 = self.client_id.encode('ascii') + b'\x00'
        await self._send_command(self.CMD_2F31, extra=extra1, is_handshake=True)
        
        # Pkt2: 2f33 短查询 [cite: 32]
        await self._send_command(self.CMD_2F33, is_handshake=True)
        
        # Pkt3: 0/1 格式重宣告 (对齐重放包) [cite: 32]
        await self._send_command(self.CMD_2F31_SHORT, is_handshake=True)
        
        # Pkt4: 切换到同步状态 (Session 0300, DstSub 5503) 
        timestamp = int(time.time())
        uuid1_bytes = self.uuid1.encode('ascii') # 24 字节 [cite: 35]
        uuid2_bytes = self.uuid2.encode('ascii') # 16 字节 [cite: 36]
        
        extra4 = (
            b'\x0a\x18' + uuid1_bytes + 
            b'\x12\x10' + uuid2_bytes + 
            b'\x1d' + struct.pack("<I", timestamp) + 
            b'\x20\xcb\xcc\xed\xcb\x06'
        )
        
        await self._send_command(
            self.CMD_2F33_LONG,
            session=self.SESSION_SYNC,
            field_cnt=2, 
            extra=extra4, 
            is_handshake=True,
            dst_sub=self.DST_SUB_SYNC
        )
        self._log("握手序列发送完毕。")

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
            print(f"[Handshake Debug] TX -> {pkt.hex()}")
        
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

    # ================= 业务与数据处理层 =================

    def _notification_handler(self, sender: Any, data: bytearray):
        # 喂狗
        self._last_data_time = time.time()

        # Session 结束包处理
        if self.CMD_2F34 in data:
            self._log("Session end packet received. Queuing ACK.")
            # 【修复】将任务推入队列，避免在回调中直接 create_task 失控
            self._tx_queue.put_nowait(("ACK", None))
            return

        # 状态判定
        magic_idx = data.find(self.MAGIC_KEY)
        new_status = BikeStatus.ACTIVE if magic_idx != -1 else BikeStatus.IDLE

        if new_status != self._current_status:
            old_status = self._current_status
            self._current_status = new_status
            if self.status_callback:
                self.status_callback(old_status, new_status)

        # 数据解析
        bike_data = None
        if new_status == BikeStatus.ACTIVE:
            try:
                start_ptr = magic_idx + 2
                if len(data) >= start_ptr + 2:
                    payload = data[start_ptr:-2]
                    res = {'rpm': 0, 'power': 0, 'duration': 0, 'resistance': 0, 'calories': 0, 'status_code': 0}
                    i = 0
                    while i < len(payload):
                        if i + 1 >= len(payload): break
                        tag = payload[i]
                        val, bytes_used = self._read_varint(payload, i + 1)
                        if tag == self.TAG_STATUS:     res['status_code'] = val
                        elif tag == self.TAG_DURATION: res['duration'] = val
                        elif tag == self.TAG_CALORIES: res['calories'] = val
                        elif tag == self.TAG_RESISTANCE: res['resistance'] = val
                        elif tag == self.TAG_RPM:      res['rpm'] = val
                        elif tag == self.TAG_POWER:    res['power'] = val
                        i += (1 + bytes_used)

                    bike_data = BikeData(**res, raw_data=data.hex())
                    self._last_calories = res['calories']
            except Exception as e:
                self._log(f"Parser Error: {e}", "ERROR")

        if bike_data is None:
            bike_data = BikeData(raw_data=data.hex())

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
                if self._current_status == BikeStatus.IDLE:
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
                if self._current_status != BikeStatus.IDLE:
                    old_status = self._current_status
                    self._current_status = BikeStatus.IDLE
                    if self.status_callback: self.status_callback(old_status, BikeStatus.IDLE)
                self.data_callback(BikeData(raw_data="RECONNECTING"))
                
                # 清理旧连接
                await self._disconnect_internal()
                
                # 重连循环
                self._log("Entering reconnection loop...")
                while self.running:
                    if await self._connect_internal():
                        self._log("Reconnected successfully! 🎉")
                        break
                    
                    self._log("Reconnect failed. Waiting 5s before next attempt...")
                    for _ in range(5):
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