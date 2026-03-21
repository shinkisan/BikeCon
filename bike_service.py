import asyncio
import json
import os
import logging
import logging.handlers
import shutil
import atexit
import glob
import signal
from pathlib import Path
from bike_driver import BikeClient, BikeData, BikeStatus

IDENTITY_PATH = Path("/etc/BikeCon/identity.json")

BIKE_MAC = None
try:
    if IDENTITY_PATH.exists():
        with IDENTITY_PATH.open(encoding="utf-8") as f:
            cfg = json.load(f)
            mac = cfg.get("bike_mac")
            if isinstance(mac, str) and mac:
                BIKE_MAC = mac
except Exception as e:
    print(f"[BikeService] Failed to load bike_mac: {e}")

if not BIKE_MAC:
    raise ValueError("[BikeService] bike_mac not found in identity.json")

RUN_DIR = Path("/var/run/BikeCon")
PUBSUB_SOCKET = RUN_DIR / "pubsub.sock"
CONTROL_SOCKET = RUN_DIR / "control.sock"
MIXER_SOCKET = RUN_DIR / "mixer.sock"
WEBAPP_SOCKET = RUN_DIR / "webapp.sock"
BIKE_ACTIVE_FLAG = RUN_DIR / "bike_active"

try:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError as e:
    raise RuntimeError(f"[BikeService] {RUN_DIR} not writable. Check systemd RuntimeDirectory/permissions.") from e

RAM_DISK_DIR = Path("/dev/shm/BikeCon")
RAM_DISK_DIR.mkdir(parents=True, exist_ok=True)
RAM_LOG_PATH = RAM_DISK_DIR / "bike_raw_data.log"
PERSISTENT_LOG_DIR = Path("/var/log/BikeCon")
PERSISTENT_LOG_DIR.mkdir(parents=True, exist_ok=True)

MAX_LOG_SIZE = 2 * 1024 * 1024
BACKUP_COUNT = 1

logger = logging.getLogger("BikeData")
logger.setLevel(logging.INFO)
handler = logging.handlers.RotatingFileHandler(
    str(RAM_LOG_PATH), maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT
)
formatter = logging.Formatter('%(asctime)s.%(msecs)03d | %(message)s', datefmt='%H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)
BIKE_DEBUG = os.getenv("BIKECON_BIKE_DEBUG", "0") == "1"


def _debug_log(msg: str):
    if BIKE_DEBUG:
        print(msg)

def save_logs_to_disk():
    print("[Log] Saving logs to disk...")
    try:
        for temp_file in glob.glob(f"{RAM_LOG_PATH}*"):
            if os.path.exists(temp_file):
                filename = os.path.basename(temp_file)
                dest_path = PERSISTENT_LOG_DIR / filename
                if os.path.abspath(temp_file) == os.path.abspath(dest_path):
                    continue
                shutil.copy2(temp_file, str(dest_path))
    except Exception as e:
        print(f"[Log] Save failed: {e}")

atexit.register(save_logs_to_disk)


class AsyncUnixClient:
    def __init__(self, path, name):
        self.path = path
        self.name = name
        self.writer = None
        self._last_error_log_ts = 0.0

    async def ensure_connection(self):
        if self.writer and not self.writer.transport.is_closing():
            return True
        try:
            _, self.writer = await asyncio.open_unix_connection(self.path)
            _debug_log(f"[BikeService] Unix client connected -> {self.name} ({self.path})")
            return True
        except Exception as e:
            now = asyncio.get_event_loop().time()
            if now - self._last_error_log_ts >= 5.0:
                _debug_log(f"[BikeService] Unix connect failed -> {self.name}: {e}")
                self._last_error_log_ts = now
            self.writer = None
            return False

    async def send(self, data):
        if await self.ensure_connection():
            try:
                self.writer.write(json.dumps(data).encode() + b'\n')
                await self.writer.drain()
            except Exception as e:
                now = asyncio.get_event_loop().time()
                if now - self._last_error_log_ts >= 5.0:
                    _debug_log(f"[BikeService] Unix send failed -> {self.name}: {e}")
                    self._last_error_log_ts = now
                self.writer = None


class BikeService:
    def __init__(self):
        self.client = BikeClient(
            BIKE_MAC,
            data_callback=self.on_data,
            status_callback=self.on_status
        )
        
        self.pubsub_writers = set()
        self.webapp = AsyncUnixClient(WEBAPP_SOCKET, "WebApp")
        self.mixer = AsyncUnixClient(MIXER_SOCKET, "Mixer")
        self.pubsub_server = None
        self.control_server = None
        self._bike_connected = False
        self._last_link_state = None
        self._last_status_msg = None

    async def handle_pubsub_connection(self, reader, writer):
        """处理 pubsub 订阅者连接"""
        self.pubsub_writers.add(writer)
        _debug_log(f"[BikeService] PubSub subscriber connected")
        try:
            while True:
                data = await reader.read(1)
                if not data:
                    break
        except:
            pass
        finally:
            self.pubsub_writers.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def handle_control_connection(self, reader, writer):
        """处理控制命令连接 (来自 FTMS 等)"""
        _debug_log(f"[BikeService] Control client connected")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode().strip())
                    await self.handle_control_message(msg)
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            print(f"[BikeService] Control error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def handle_control_message(self, msg: dict):
        """处理控制消息"""
        cmd_type = msg.get("type")
        _debug_log(f"[BikeService] Control: {cmd_type} - {msg}")
        
        if cmd_type == "set_resistance":
            level = msg.get("level", 10)
            await self.client.set_resistance(level)
        elif cmd_type == "start":
            await self.client.start_bike()
        elif cmd_type == "stop":
            await self.client.stop_bike()
        elif cmd_type == "pause":
            await self.client.pause_bike()
        elif cmd_type == "wake":
            await self.client.wake_bike()

    def broadcast_to_subscribers(self, data: dict):
        """广播数据到所有订阅者"""
        msg = json.dumps(data).encode() + b'\n'
        msg_type = data.get("type")
        if msg_type in ("bike_link", "bike_status"):
            _debug_log(f"[BikeService] Broadcast {msg_type} to pubsub subscribers={len(self.pubsub_writers)} payload={data}")
        for writer in list(self.pubsub_writers):
            try:
                writer.write(msg)
            except:
                pass

    async def start_servers(self):
        # 启动 pubsub 服务器
        if PUBSUB_SOCKET.exists():
            PUBSUB_SOCKET.unlink()
        self.pubsub_server = await asyncio.start_unix_server(
            self.handle_pubsub_connection,
            path=str(PUBSUB_SOCKET)
        )
        os.chmod(PUBSUB_SOCKET, 0o666)
        _debug_log(f"[BikeService] PubSub server: {PUBSUB_SOCKET}")
        
        # 启动 control 服务器
        if CONTROL_SOCKET.exists():
            CONTROL_SOCKET.unlink()
        self.control_server = await asyncio.start_unix_server(
            self.handle_control_connection,
            path=str(CONTROL_SOCKET)
        )
        os.chmod(CONTROL_SOCKET, 0o666)
        _debug_log(f"[BikeService] Control server: {CONTROL_SOCKET}")

    def on_data(self, data: BikeData):
        if data.raw_data == "RECONNECTING":
            _debug_log(f"[BikeService] on_data RECONNECTING received (was_connected={self._bike_connected})")
            if self._bike_connected:
                self._bike_connected = False
            link_msg = {"type": "bike_link", "connected": False}
            self._last_link_state = link_msg
            self.broadcast_to_subscribers(link_msg)
            asyncio.create_task(self.webapp.send(link_msg))
            asyncio.create_task(self.mixer.send(link_msg))
            return
        if not self._bike_connected:
            self._bike_connected = True
            link_msg = {"type": "bike_link", "connected": True}
            self._last_link_state = link_msg
            _debug_log("[BikeService] First bike data after offline -> bike_link connected=True")
            self.broadcast_to_subscribers(link_msg)
            asyncio.create_task(self.webapp.send(link_msg))
            asyncio.create_task(self.mixer.send(link_msg))

        msg = {
            "type": "bike_data",
            "rpm": data.rpm,
            "power": data.power,
            "speed": data.speed,
            "distance": data.distance,
            "duration": data.duration,
            "resistance": data.resistance,
            "calories": data.calories,
            "status": data.status_code
        }
        
        log_msg = (
            f"HEX: {data.raw_data if data.raw_data else 'N/A':<48} | "
            f"RPM: {data.rpm:<3} | PWR: {data.power:<3} | "
            f"SPD: {data.speed:<4.1f} | DST: {data.distance:<5} | "
            f"RES: {data.resistance:<2} | TIME: {data.duration:<4} | "
            f"KCAL: {data.calories:<3.1f} | STA: {data.status_code:02X}"
        )
        
        logger.info(log_msg)
        
        # 广播到 pubsub 订阅者
        self.broadcast_to_subscribers(msg)
        
        # 发送到 WebApp 和 Mixer
        asyncio.create_task(self.webapp.send(msg))
        asyncio.create_task(self.mixer.send(msg))

    def on_status(self, old_status, new_status):
        is_active = (new_status == BikeStatus.ACTIVE)
        print(f"\n[BikeService] Status: {old_status.name} -> {new_status.name}")

        if not self._bike_connected:
            self._bike_connected = True
            link_msg = {"type": "bike_link", "connected": True}
            self._last_link_state = link_msg
            _debug_log("[BikeService] on_status forced bike_link connected=True (status arrived before data)")
            self.broadcast_to_subscribers(link_msg)
            asyncio.create_task(self.webapp.send(link_msg))
            asyncio.create_task(self.mixer.send(link_msg))

        try:
            if is_active:
                BIKE_ACTIVE_FLAG.write_text("1")
            else:
                if BIKE_ACTIVE_FLAG.exists():
                    BIKE_ACTIVE_FLAG.unlink()
        except Exception as e:
            print(f"Flag Error: {e}")

        status_msg = {
            "type": "bike_status",
            "active": is_active,
            "status_name": new_status.name,
            "status_code": new_status.value
        }
        self._last_status_msg = status_msg
        
        self.broadcast_to_subscribers(status_msg)
        asyncio.create_task(self.webapp.send(status_msg))
        asyncio.create_task(self.mixer.send(status_msg))

    async def run(self):
        print(f"[BikeService] Starting ({BIKE_MAC})...")
        
        await self.start_servers()
        
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def signal_handler():
            print("[BikeService] Shutting down...")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                pass

        try:
            await self.client.start()
            print(f"[BikeService] BikeClient started")
            await stop_event.wait()
        except Exception as e:
            print(f"[BikeService] Error: {e}")
        finally:
            print("[BikeService] Stopping BikeClient...")
            await self.client.stop()
            
            if self.pubsub_server:
                self.pubsub_server.close()
            if self.control_server:
                self.control_server.close()
            print("[BikeService] Exited")


if __name__ == "__main__":
    service = BikeService()
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        pass
