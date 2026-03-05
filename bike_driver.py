import asyncio
import time
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any
from pathlib import Path
import json
from bleak import BleakClient, BleakScanner

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
    CMD_2F31 = b'\xb3\x31\x2f\x31'  # "1/1"
    CMD_2F33 = b'\xb3\x30\x2f\x33'  # "0/3"
    CMD_2F37 = b'\xb5\x31\x30\x36\x2f\x37'  # "106/7"
    CMD_2F34 = b'\xb5\x31\x30\x36\x2f\x34'  # "106/4"
    CMD_2F38 = b'\xb5\x31\x30\x36\x2f\x38'  # "106/8"
    FRAME_MAGIC = b'\xA5\xA5\xA0'
    HEARTBEAT_INTERVAL = 1.0

    def __init__(self, 
                 mac_address: str, 
                 data_callback: Callable[[BikeData], None],
                 status_callback: Optional[Callable[[BikeStatus, BikeStatus], None]] = None):
        self.mac_address = mac_address
        self.data_callback = data_callback
        self.status_callback = status_callback
        
        self.client: Optional[BleakClient] = None
        self.running = False  # 全局运行开关 (用户意图)
        self._seq = 0
        self._app_counter = 0xA4  # Start from 0xA4 as per logs
        self._current_status = BikeStatus.IDLE
        self._last_data_time = 0.0
        self._last_calories = 0
        
        # Identity data
        self.phone_mac = ""
        self.uuid1 = ""
        self.uuid2 = ""
        
        # 任务句柄
        self._watchdog_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # Load identity and build packets
        self._load_identity()
        self._build_handshake_packets()

    def _load_identity(self):
        """
        Load identity data from identity.json.
        """
        try:
            identity_path = Path(__file__).parent / "identity.json"
            if identity_path.exists():
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
                self.phone_mac = identity.get("phone_mac", "").replace(":", "").lower()
                self.uuid1 = identity.get("uuid1", "")
                self.uuid2 = identity.get("uuid2", "")
            else:
                raise ValueError("identity.json not found")
        except Exception as e:
            raise ValueError(f"[BikeDriver] Failed to load identity.json: {e}")
        
        if not all([self.phone_mac, self.uuid1, self.uuid2]):
            raise ValueError("[BikeDriver] CRITICAL: Missing required fields in identity.json")

    def _build_handshake_packets(self):
        """
        Build handshake packets dynamically from identity data.
        """
        self.HANDSHAKE_PACKETS = []
        
        # Pkt1: 2f31 with phone MAC
        extra1 = bytes.fromhex(self.phone_mac) + b'\x00'
        payload1 = self._build_payload(self.CMD_2F31, extra=extra1)
        self.HANDSHAKE_PACKETS.append(self._build_frame(payload1))
        
        # Pkt2: 2f33 empty
        payload2 = self._build_payload(self.CMD_2F33)
        self.HANDSHAKE_PACKETS.append(self._build_frame(payload2))
        
        # Pkt3: 2f31 empty
        payload3 = self._build_payload(self.CMD_2F31)
        self.HANDSHAKE_PACKETS.append(self._build_frame(payload3))
        
        # Pkt4: 2f33 with UUIDs and timestamp
        timestamp = int(time.time())
        uuid1_bytes = bytes.fromhex(self.uuid1)
        uuid2_bytes = bytes.fromhex(self.uuid2)
        extra4 = (
            b'\x0a\x18' + uuid1_bytes + 
            b'\x12\x10' + uuid2_bytes + 
            b'\x1d' + struct.pack("<I", timestamp) + 
            b'\x20\xcb\xcc\xed\xcb\x06\x53\x2b'
        )
        payload4 = self._build_payload(self.CMD_2F33, field_cnt=2, extra=extra4)
        self.HANDSHAKE_PACKETS.append(self._build_frame(payload4))
        
        print(f"[BikeDriver] ✓ Handshake packets built: {len(self.HANDSHAKE_PACKETS)} packet(s)")

    def _build_payload(self, cmd_str, session=None, field_cnt=0, extra=b''):
        """
        Build payload for a packet.
        """
        if session is None:
            session = self.SESSION_IDLE
        app_cnt = struct.pack("<H", self._next_app_counter())
        payload = (
            self.SRC_ID_PHONE + self.DST_SUB_PHONE + self.MSG_TYPE + app_cnt + 
            session + bytes([field_cnt]) + self.FIXED_BYTE + cmd_str
        )
        if extra:
            payload += b'\xFF' + extra
        return payload

    def _next_app_counter(self):
        """
        Get next app counter, increment by 257, uint16 wrap.
        """
        cnt = self._app_counter
        self._app_counter = (self._app_counter + 257) & 0xFFFF
        return cnt

    def _build_frame(self, payload):
        """
        Build complete frame with magic, seq, length, payload, crc.
        """
        body = self.FRAME_MAGIC + struct.pack("B", self._seq) + struct.pack("<H", len(payload)) + payload
        crc = self._crc16(body)
        self._seq = (self._seq + 1) % 256
        return body + struct.pack("<H", crc)

    def _load_config(self):
        """
        Load device-specific handshake and heartbeat payload from config.json.
        Raises ValueError if required fields are missing.
        """
        try:
            cfg_path = Path(__file__).parent / "config.json"
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                hs = cfg.get("handshake_packets")
                hb = cfg.get("heartbeat_payload")
                
                if isinstance(hs, list) and hs:
                    # Convert hex strings to bytes
                    self.HANDSHAKE_PACKETS = [bytes.fromhex(x) for x in hs if isinstance(x, str)]
                
                if isinstance(hb, str) and hb:
                    self.HEARTBEAT_PAYLOAD = hb
        except json.JSONDecodeError as e:
            raise ValueError(f"[BikeDriver] config.json is invalid JSON: {e}")
        except Exception as e:
            raise ValueError(f"[BikeDriver] Failed to load config.json: {e}")
        
        # Ensure both required fields are loaded
        if not self.HANDSHAKE_PACKETS:
            raise ValueError(
                "[BikeDriver] CRITICAL: 'handshake_packets' not found or empty in config.json. "
                "This field is required and contains device-specific Bluetooth handshake data. "
                "Please configure it locally in config.json (add to .gitignore to keep it private)."
            )
        if not self.HEARTBEAT_PAYLOAD:
            raise ValueError(
                "[BikeDriver] CRITICAL: 'heartbeat_payload' not found or empty in config.json. "
                "This field is required and contains device-specific heartbeat data. "
                "Please configure it locally in config.json (add to .gitignore to keep it private)."
            )
        
        # Log successful configuration load
        print(f"[BikeDriver] ✓ Configuration loaded successfully from config.json")
        print(f"[BikeDriver]   - Handshake packets: {len(self.HANDSHAKE_PACKETS)} packet(s)")
        for i, pkt in enumerate(self.HANDSHAKE_PACKETS):
            hex_str = pkt.hex()
            print(f"[BikeDriver]     [{i+1}] {len(pkt)} bytes: {hex_str}")
        hb_hex = self.HEARTBEAT_PAYLOAD
        print(f"[BikeDriver]   - Heartbeat payload: {len(hb_hex)//2} bytes: {hb_hex}")

    def _read_varint(self, data: bytes, start_idx: int):
        res = 0
        shift = 0
        count = 0
        for i in range(start_idx, len(data)):
            byte = data[i]
            res |= (byte & 0x7F) << shift
            count += 1
            if not (byte & 0x80):
                break
            shift += 7
        return res, count

    def _write_varint(self, value: int) -> bytes:
        """Encode integer as varint."""
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

    def _is_session_end_packet(self, data: bytearray) -> bool:
        """Check if data contains session end command (2f34)."""
        # Look for CMD_2F34 in payload
        try:
            if len(data) < 10: return False
            # Skip frame header, find payload
            payload_start = 5  # After magic(3) + seq(1) + len(2)
            if payload_start + len(self.CMD_2F34) <= len(data) - 2:  # -2 for CRC
                payload = data[payload_start:-2]
                return self.CMD_2F34 in payload
        except:
            pass
        return False

    async def _send_session_end_ack(self):
        """Send session end acknowledgment."""
        try:
            payload = self._build_payload(self.CMD_2F34, extra=b'\x01')  # field1=1
            pkt = self._build_frame(payload)
            await self.client.write_gatt_char(self.CHAR_UUID, pkt, response=False)
            print("[BikeDriver] Sent session end ACK")
        except Exception as e:
            print(f"[BikeDriver] Failed to send session end ACK: {e}")

    def _build_heartbeat_idle(self):
        """Build IDLE heartbeat payload (19 bytes)."""
        return self._build_payload(self.CMD_2F37, session=self.SESSION_ACTIVE)

    def _build_heartbeat_active(self, calories: int):
        """Build ACTIVE heartbeat payload (23 bytes) with dynamic calories."""
        varint_calories = self._write_varint(calories * 1000)
        extra = b'\x20' + varint_calories  # field4
        return self._build_payload(self.CMD_2F37, session=self.SESSION_ACTIVE, field_cnt=0, extra=extra)

    def _build_packet(self, payload_hex: str) -> bytes:
        payload = bytes.fromhex(payload_hex)
        header = b'\xA5\xA5\xA0'
        body = header + struct.pack("B", self._seq) + struct.pack("<H", len(payload)) + payload
        crc = self._crc16(body)
        self._seq = (self._seq + 1) % 256
        return body + struct.pack("<H", crc)

    def _notification_handler(self, sender: Any, data: bytearray):
        # print(f"[Notification] Raw data: {data.hex()}")
        # 1. 喂狗
        self._last_data_time = time.time()

        # 2. 检查是否是会话结束包 (2f34)
        if self._is_session_end_packet(data):
            asyncio.create_task(self._send_session_end_ack())
            return

        # 3. 状态判定
        magic_idx = data.find(self.MAGIC_KEY)
        new_status = BikeStatus.ACTIVE if magic_idx != -1 else BikeStatus.IDLE

        # 4. 状态回调 (仅变化时触发)
        if new_status != self._current_status:
            old_status = self._current_status
            self._current_status = new_status
            if self.status_callback:
                self.status_callback(old_status, new_status)

        # 4. 数据解析
        bike_data = None

        if new_status == BikeStatus.ACTIVE:
            try:
                # 截取 Payload (排除 CRC)
                start_ptr = magic_idx + 2
                # 安全检查：长度不够直接返回
                if len(data) >= start_ptr + 2:
                    payload = data[start_ptr:-2]
                    res = {'rpm': 0, 'power': 0, 'duration': 0, 'resistance': 0, 'calories': 0, 'status_code': 0}
                    i = 0
                    
                    while i < len(payload):
                        # 防止越界读取
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

                    bike_data = BikeData(
                        rpm=res['rpm'], power=res['power'], duration=res['duration'],
                        resistance=res['resistance'], calories=res['calories'],
                        status_code=res['status_code'], raw_data=data.hex()
                    )
                    # Update last calories for heartbeat
                    self._last_calories = res['calories']
            except Exception as e:
                print(f"[Parser Error] {e}")
                # 解析失败也要返回原始数据
                bike_data = BikeData(raw_data=data.hex())

        # 如果没有成功解析出 Active 数据，或者是 IDLE 状态
        if bike_data is None:
            bike_data = BikeData(raw_data=data.hex())

        # 5. 统一触发回调 (只触发一次)
        self.data_callback(bike_data)

    async def _heartbeat_loop(self):
        print("[Heartbeat] Loop started.")
        while self.running and self.client and self.client.is_connected:
            try:
                if self._current_status == BikeStatus.IDLE:
                    payload = self._build_heartbeat_idle()
                else:
                    payload = self._build_heartbeat_active(self._last_calories)
                pkt = self._build_frame(payload)
                await self.client.write_gatt_char(self.CHAR_UUID, pkt, response=False)
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            except Exception:
                # 发送失败通常意味着连接断了，不需要这里处理，交给看门狗即可
                break
        print("[Heartbeat] Loop stopped.")

    async def _watchdog_loop(self):
        print("[Watchdog] Guard started.")
        # 此时建议用较长的超时，避免握手期误杀
        TIMEOUT_LIMIT = 20.0
        
        while self.running:
            await asyncio.sleep(2.0)
            
            # 标记是否需要重连
            should_reconnect = False
            
            # 情况 A: 客户端对象都没了，或者底层断开了
            if not self.client or not self.client.is_connected:
                print("[Watchdog] ⚠️ Connection lost detected!")
                should_reconnect = True
            
            # 情况 B: 连接着，但是很久没收到数据了
            elif time.time() - self._last_data_time > TIMEOUT_LIMIT:
                print(f"[Watchdog] ⚠️ Data timeout! Last seen: {time.time() - self._last_data_time:.1f}s ago")
                should_reconnect = True
                
            # 执行重连流程
            if should_reconnect:
                # 1. UI 归零
                self._current_status = BikeStatus.IDLE
                # 通知外部正在重连
                try:
                    self.data_callback(BikeData(rpm=0, power=0, raw_data="RECONNECTING"))
                    if self.status_callback:
                        self.status_callback(BikeStatus.ACTIVE, BikeStatus.IDLE)
                except Exception as e:
                    print(f"[Watchdog] Callback error: {e}")
                
                # 2. 清理旧连接 (is_retry=True)
                await self.disconnect(is_retry=True)
                
                # 3. 自动重连循环
                print("[Watchdog] Entering reconnection loop...")
                while self.running:
                    print("[Watchdog] Retrying connection...")
                    if await self.connect():
                        print("[Watchdog] Reconnected successfully! 🎉")
                        break
                    
                    print("[Watchdog] Connect failed. Retrying in 5s...")
                    # 等待期间也要检测 running 状态
                    for _ in range(5):
                        if not self.running: break
                        await asyncio.sleep(1.0)
                
                # 如果重连循环是因为 running=False 而退出的
                if not self.running:
                    break
        
        print("[Watchdog] Guard stopped.")

    async def connect(self) -> bool:
        # 防止重复连接
        if self.client and self.client.is_connected:
            return True

        print(f"[BikeDriver] Connecting to {self.mac_address}...")
        try:
            self.running = True 
            
            # 【核心修改1】直接通过 MAC 地址创建客户端，跳过扫描步骤
            # 这能有效避免 org.bluez.Error.InProgress 错误
            self.client = BleakClient(self.mac_address)
            
            # 【核心修改2】增加连接超时控制 (20秒)
            # 如果底层卡住，这里会抛出 asyncio.TimeoutError
            await asyncio.wait_for(self.client.connect(), timeout=20.0)
            
            if self.client.is_connected:
                print(f"[BikeDriver] Connected directly to MAC!")
                
                # 1. 刚连上时喂一次狗 (防止初始化期间被看门狗误杀)
                self._last_data_time = time.time()
                
                await self.client.start_notify(self.CHAR_UUID, self._notification_handler)
                
                # 发送握手
                for pkt in self.HANDSHAKE_PACKETS:
                    await self.client.write_gatt_char(self.CHAR_UUID, pkt, response=False)
                
                # 2. 握手结束后再次喂狗 (重置计时器)
                self._last_data_time = time.time()
                
                # 启动心跳
                if self._heartbeat_task: self._heartbeat_task.cancel()
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                
                # 启动看门狗 (注意：确保看门狗不会重复启动)
                if self._watchdog_task is None or self._watchdog_task.done():
                    self._watchdog_task = asyncio.create_task(self._watchdog_loop())
                
                return True
                
        except Exception as e:
            print(f"[BikeDriver] Connect Error: {e}")
            # 【核心修改3】确保清理干净
            if self.client:
                try:
                    await self.client.disconnect()
                except:
                    pass
                self.client = None
            
            # 【核心修改4】失败后强制等待1秒，让 BlueZ 释放资源
            # 这对防止连续的 InProgress 错误非常关键
            await asyncio.sleep(1.0)
                
        return False

    async def disconnect(self, is_retry=False):
        """
        断开连接。
        :param is_retry: True=重连过程中的清理(保留看门狗和运行状态); False=彻底停止(杀掉一切)
        """
        if not is_retry:
            print("[BikeDriver] Stopping driver completely...")
            self.running = False # 这会让看门狗循环退出
            if self._watchdog_task:
                self._watchdog_task.cancel()
                try: await self._watchdog_task 
                except: pass
                self._watchdog_task = None
        else:
            print("[BikeDriver] temporary disconnect for retry...")

        # 停止心跳
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try: await self._heartbeat_task
            except: pass
            self._heartbeat_task = None

        # 断开蓝牙
        if self.client:
            try: await self.client.disconnect()
            except: pass
            self.client = None
        
        print("[BikeDriver] Disconnected.")