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

PUBSUB_SOCKET = "/var/run/BikeCon/pubsub.sock"
CONTROL_SOCKET = "/var/run/BikeCon/control.sock"
MIXER_SOCKET = "/var/run/BikeCon/mixer.sock"
WEBAPP_SOCKET = "/var/run/BikeCon/webapp.sock"
BIKE_ACTIVE_FLAG = "/var/run/BikeCon/bike_active"

try:
    os.makedirs("/var/run/BikeCon", exist_ok=True)
except PermissionError:
    PUBSUB_SOCKET = "/tmp/BikeCon/pubsub.sock"
    CONTROL_SOCKET = "/tmp/BikeCon/control.sock"
    MIXER_SOCKET = "/tmp/BikeCon/mixer.sock"
    WEBAPP_SOCKET = "/tmp/BikeCon/webapp.sock"
    BIKE_ACTIVE_FLAG = "/tmp/BikeCon/bike_active"
    os.makedirs("/tmp/BikeCon", exist_ok=True)

RAM_DISK_DIR = "/dev/shm/BikeCon"
os.makedirs(RAM_DISK_DIR, exist_ok=True)
RAM_LOG_PATH = os.path.join(RAM_DISK_DIR, "bike_raw_data.log")
PERSISTENT_LOG_DIR = "/var/log/BikeCon"
os.makedirs(PERSISTENT_LOG_DIR, exist_ok=True)

MAX_LOG_SIZE = 2 * 1024 * 1024
BACKUP_COUNT = 1

logger = logging.getLogger("BikeData")
logger.setLevel(logging.INFO)
handler = logging.handlers.RotatingFileHandler(
    RAM_LOG_PATH, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT
)
formatter = logging.Formatter('%(asctime)s.%(msecs)03d | %(message)s', datefmt='%H:%M:%S')
handler.setFormatter(formatter)
logger.addHandler(handler)

def save_logs_to_disk():
    print("[Log] Saving logs to disk...")
    try:
        for temp_file in glob.glob(f"{RAM_LOG_PATH}*"):
            if os.path.exists(temp_file):
                filename = os.path.basename(temp_file)
                dest_path = os.path.join(PERSISTENT_LOG_DIR, filename)
                if os.path.abspath(temp_file) == os.path.abspath(dest_path):
                    continue
                shutil.copy2(temp_file, dest_path)
    except Exception as e:
        print(f"[Log] Save failed: {e}")

atexit.register(save_logs_to_disk)


class AsyncUnixClient:
    def __init__(self, path, name):
        self.path = path
        self.name = name
        self.writer = None

    async def ensure_connection(self):
        if self.writer and not self.writer.transport.is_closing():
            return True
        try:
            _, self.writer = await asyncio.open_unix_connection(self.path)
            return True
        except:
            self.writer = None
            return False

    async def send(self, data):
        if await self.ensure_connection():
            try:
                self.writer.write(json.dumps(data).encode() + b'\n')
                await self.writer.drain()
            except:
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

    async def handle_pubsub_connection(self, reader, writer):
        """处理 pubsub 订阅者连接"""
        self.pubsub_writers.add(writer)
        print(f"[BikeService] PubSub subscriber connected")
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
        print(f"[BikeService] Control client connected")
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
        print(f"[BikeService] Control: {cmd_type} - {msg}")
        
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
        for writer in list(self.pubsub_writers):
            try:
                writer.write(msg)
            except:
                pass

    async def start_servers(self):
        # 启动 pubsub 服务器
        if os.path.exists(PUBSUB_SOCKET):
            os.remove(PUBSUB_SOCKET)
        self.pubsub_server = await asyncio.start_unix_server(
            self.handle_pubsub_connection,
            path=PUBSUB_SOCKET
        )
        os.chmod(PUBSUB_SOCKET, 0o666)
        print(f"[BikeService] PubSub server: {PUBSUB_SOCKET}")
        
        # 启动 control 服务器
        if os.path.exists(CONTROL_SOCKET):
            os.remove(CONTROL_SOCKET)
        self.control_server = await asyncio.start_unix_server(
            self.handle_control_connection,
            path=CONTROL_SOCKET
        )
        os.chmod(CONTROL_SOCKET, 0o666)
        print(f"[BikeService] Control server: {CONTROL_SOCKET}")

    def on_data(self, data: BikeData):
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

        try:
            if is_active:
                with open(BIKE_ACTIVE_FLAG, 'w') as f:
                    f.write("1")
            else:
                if os.path.exists(BIKE_ACTIVE_FLAG):
                    os.remove(BIKE_ACTIVE_FLAG)
        except Exception as e:
            print(f"Flag Error: {e}")

        status_msg = {
            "type": "bike_status",
            "active": is_active,
            "status_name": new_status.name,
            "status_code": new_status.value
        }
        
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
