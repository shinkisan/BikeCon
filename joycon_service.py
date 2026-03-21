import asyncio
import evdev
import socket
import json
import time
import sys
import re
import os
import subprocess
import errno
from evdev import ecodes, InputDevice, list_devices
from pathlib import Path

# Socket path follows FHS standard
SOCKET_PATH = "/var/run/BikeCon/mixer.sock"
WEBAPP_SOCKET = "/var/run/BikeCon/webapp.sock"

# Fallback if /var/run is not writable
try:
    os.makedirs("/var/run/BikeCon", exist_ok=True)
except (PermissionError, OSError):
    SOCKET_PATH = "/tmp/BikeCon/mixer.sock"
    WEBAPP_SOCKET = "/tmp/BikeCon/webapp.sock"
    os.makedirs("/tmp/BikeCon", exist_ok=True)
IDLE_TIMEOUT = 600 # 普通手柄 10分钟无操作断开
IMU_IDLE_TIMEOUT = 2 # IMU 2秒无数据视为手动关闭，强制断开
BIKE_ACTIVE_FLAG = "/tmp/c2lite_bike_active"

# MixerClient 负责与混合器进程通信，向Mixer发送手柄状态更新
class MixerClient:
    def __init__(self, path):
        self.path = path
        self.writer = None
        self.lock = asyncio.Lock() 

    async def connect(self):
        try:
            _, self.writer = await asyncio.open_unix_connection(self.path)
        except Exception:
            self.writer = None

    async def send(self, payload):
        if not self.writer:
            await self.connect()
        
        if self.writer:
            async with self.lock:
                try:
                    data = (json.dumps(payload) + "\n").encode()
                    self.writer.write(data)
                    await self.writer.drain()
                except Exception:
                    self.writer = None

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
        except Exception:
            self.writer = None
            return False

    async def send(self, data):
        if await self.ensure_connection():
            try:
                self.writer.write(json.dumps(data).encode() + b'\n')
                await self.writer.drain()
            except Exception:
                self.writer = None

def _read_text(path: Path):
    try:
        return path.read_text().strip()
    except Exception:
        return None

def _normalize_mac(text: str):
    if not text:
        return None
    return re.sub(r'[^0-9a-fA-F]', '', text).upper()

def _battery_from_sysfs(mac: str, name: str, is_left: bool):
    base = Path("/sys/class/power_supply")
    if not base.exists():
        return None
    best = None
    best_score = 0
    for supply in base.iterdir():
        cap_level = _read_text(supply / "capacity_level")
        if not cap_level:
            continue
        supply_name = supply.name
        score = 0
        if "nintendo_switch_controller_battery" in supply_name:
            score += 2
        if is_left and "2006" in supply_name:
            score += 3
        if (not is_left) and "2007" in supply_name:
            score += 3
        if score > best_score:
            best_score = score
            level_map = {
                "Full": 100,
                "High": 75,
                "Normal": 50,
                "Low": 25,
                "Critical": 5
            }
            best = level_map.get(cap_level, None)
    return best

def get_battery_level(mac: str, name: str, is_left: bool):
    level = _battery_from_sysfs(mac, name, is_left)
    if level is not None:
        return max(0, min(100, level))
    return None

def get_device_mac(device):
    if device.uniq and len(device.uniq) == 17:
        return device.uniq.upper()
    match = re.search(r'([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', device.phys)
    if match:
        return match.group(0).upper()
    return None

def map_value(value, min_v, max_v):
    if max_v == min_v: return 128
    val = (value - min_v) * 255 / (max_v - min_v)
    val = int(max(0, min(255, val)))
    if 110 < val < 146: val = 128
    return int(val)

class JoyConHandler:
    def __init__(self, device, mixer_client, webapp_client, is_imu=False):
        self.mixer = mixer_client
        self.webapp = webapp_client
        self.is_imu = is_imu
        self.name = device.name.lower()
        self.is_left = "left" in self.name or "(l)" in self.name
        self.side = "LEFT" if self.is_left else "RIGHT"
        self.is_connected = True
        self.last_activity = time.time()
        self.mac = get_device_mac(device)
        self.device = device
        self.last_battery = None
        self.last_battery_sent = 0
        # 记录关联的 loop 任务，以便 monitor 可以取消它
        self.read_task = None 
        self.battery_task = None
        
        try:
            self.abs_x = device.absinfo(ecodes.ABS_X)
            self.abs_y = device.absinfo(ecodes.ABS_Y)
            self.abs_rx = device.absinfo(ecodes.ABS_RX)
            self.abs_ry = device.absinfo(ecodes.ABS_RY)
            self.min_x, self.max_x = self.abs_x.min, self.abs_x.max
            self.min_y, self.max_y = self.abs_y.min, self.abs_y.max
            self.min_rx, self.max_rx = self.abs_rx.min, self.abs_rx.max
            self.min_ry, self.max_ry = self.abs_ry.min, self.abs_ry.max
            if self.min_x == self.max_x: self.min_x, self.max_x = 0, 255
            if self.min_y == self.max_y: self.min_y, self.max_y = 0, 255
            if self.min_rx == self.max_rx: self.min_rx, self.max_rx = 0, 255
            if self.min_ry == self.max_ry: self.min_ry, self.max_ry = 0, 255
        except:
            self.min_x, self.max_x = 0, 255
            self.min_y, self.max_y = 0, 255
            self.min_rx, self.max_rx = 0, 255
            self.min_ry, self.max_ry = 0, 255

        self.cache = [128, 128, 128, 128] 
        print(f"[{self.side}] {'IMU' if is_imu else 'Controller'} Ready: {self.mac}")
        if not is_imu:
            asyncio.create_task(self.mixer.send({"type": "source", "val": "physical"}))
            self.battery_task = asyncio.create_task(self.battery_monitor())
    
    async def loop(self):
        try:
            async for event in self.device.async_read_loop():
                if not self.is_connected: break
                await self.process_event(event)
        except OSError as e:
            # 忽略设备关闭引发的错误
            if e.errno not in (errno.EBADF, errno.ENODEV):
                print(f"[{self.side}] IO Error: {e}")
        except asyncio.CancelledError:
            pass # 任务被取消是预期的
        except Exception:
            pass
        finally:
            self.is_connected = False
            if not self.is_imu:
                asyncio.create_task(self.mixer.send({"type": "source", "val": "virtual"}))
                await self.send_battery_update(None, connected=False)

    async def send_battery_update(self, percent, connected=True):
        msg = {
            "type": "joycon_battery",
            "side": self.side,
            "percent": percent,
            "connected": connected,
            "mac": self.mac
        }
        await self.webapp.send(msg)

    async def battery_monitor(self):
        while self.is_connected:
            level = get_battery_level(self.mac, self.name, self.is_left)
            now = time.time()
            changed = level != self.last_battery
            stale = (now - self.last_battery_sent) > 300
            if changed or stale:
                self.last_battery = level
                self.last_battery_sent = now
                await self.send_battery_update(level, connected=True)
            await asyncio.sleep(30)

    async def process_event(self, event):
        if self.is_imu:
            self.last_activity = time.time()
            return
        activity = False
        if event.type == ecodes.EV_ABS:
            idx = -1
            val = 128
            if event.code == ecodes.ABS_X:
                idx = 0
                val = map_value(event.value, self.min_x, self.max_x)
            elif event.code == ecodes.ABS_Y:
                idx = 1
                val = map_value(event.value, self.min_y, self.max_y)
            elif event.code == ecodes.ABS_RX:
                idx = 2
                val = map_value(event.value, self.min_rx, self.max_rx)
            elif event.code == ecodes.ABS_RY:
                idx = 3
                val = map_value(event.value, self.min_ry, self.max_ry)
            if idx != -1 and val != self.cache[idx]:
                self.cache[idx] = val
                activity = True
                stick = "left" if idx < 2 else "right"
                await self.mixer.send({
                    "type": "axis", "source": "physical", "stick": stick,
                    "x": self.cache[0] if stick=="left" else self.cache[2],
                    "y": self.cache[1] if stick=="left" else self.cache[3]
                })
        elif event.type == ecodes.EV_KEY:
            val = event.value
            activity = True
            BTN_MAP = {
                ecodes.BTN_SOUTH: 1, ecodes.BTN_EAST: 2, ecodes.BTN_NORTH: 4, ecodes.BTN_WEST: 3,
                ecodes.BTN_TR: 6, ecodes.BTN_START: 8, ecodes.BTN_THUMBR: 10, ecodes.BTN_MODE: 12,
                ecodes.BTN_TL: 5, ecodes.BTN_SELECT: 7, ecodes.BTN_THUMBL: 9, ecodes.BTN_Z: 11,
                ecodes.BTN_DPAD_UP: 13, ecodes.BTN_DPAD_DOWN: 14, ecodes.BTN_DPAD_LEFT: 15, ecodes.BTN_DPAD_RIGHT: 16
            }
            if event.code in BTN_MAP:
                await self.mixer.send({
                    "type": "input", "source": "physical", "target": "button", 
                    "id": BTN_MAP[event.code], "val": val
                })
            elif event.code == ecodes.BTN_TL2:
                await self.mixer.send({"type": "trigger", "source": "physical", "lr": 0, "val": 255 if val else 128})
            elif event.code == ecodes.BTN_TR2:
                await self.mixer.send({"type": "trigger", "source": "physical", "lr": 1, "val": 255 if val else 128})
        if activity:
            self.last_activity = time.time()

    async def idle_monitor(self):
        """标准监控：IMU快速断开 + 普通手柄超时断开 + 物理断开"""
        while self.is_connected:
            await asyncio.sleep(1) 
            
            if not os.path.exists(self.device.path):
                self.is_connected = False
                break
            
            if os.path.exists(BIKE_ACTIVE_FLAG):
                self.last_activity = time.time()
                continue
            
            idle_time = time.time() - self.last_activity
            should_disconnect = False
            
            # 判断是否需要断开
            if self.is_imu:
                if idle_time > IMU_IDLE_TIMEOUT:
                    print(f"[{self.side}] IMU ({int(idle_time)}s 无数据)，强制复位...")
                    should_disconnect = True
            elif idle_time > IDLE_TIMEOUT:
                print(f"[Power] 闲置超时，断开 {self.mac}")
                should_disconnect = True
            
            if should_disconnect:
                self.is_connected = False
                if self.read_task and not self.read_task.done():
                    self.read_task.cancel()
                    try:
                        await self.read_task
                    except (asyncio.CancelledError, Exception):
                        pass
                
                await asyncio.sleep(0.5) # 等待读取任务完全结束，避免设备被占用无法关闭

                try:
                    self.device.close()
                except:
                    pass
                
                if self.mac:
                    subprocess.run(["bluetoothctl", "disconnect", self.mac], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print(f"[{self.side}] 已断开 {self.mac}")
                break

async def main():
    mixer_client = MixerClient(SOCKET_PATH)
    webapp_client = AsyncUnixClient(WEBAPP_SOCKET, "WebApp")
    print("正在扫描 Joy-Con...")
    connected_paths = set()

    while True:
        try:
            devices = [InputDevice(p) for p in list_devices()]
            for dev in devices:
                if dev.path in connected_paths: continue
                
                if "joy-con" in dev.name.lower():
                    is_imu = "imu" in dev.name.lower()
                    handler = JoyConHandler(dev, mixer_client, webapp_client, is_imu)
                    connected_paths.add(dev.path)
                    
                    loop_task = asyncio.create_task(handler.loop())
                    monitor_task = asyncio.create_task(handler.idle_monitor())
                    
                    # 将 loop 任务句柄传给 handler，方便 monitor 取消它
                    handler.read_task = loop_task
                    
                    def cleanup(t, p=dev.path, m=monitor_task, l=loop_task):
                        connected_paths.discard(p)
                        if t == l and not m.done(): m.cancel()
                        if t == m and not l.done(): l.cancel()
                    
                    loop_task.add_done_callback(cleanup)
                    monitor_task.add_done_callback(cleanup)
                    
        except Exception:
            pass
        await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
