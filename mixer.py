import socket
import struct
import os
import json
import asyncio
import sys
from pathlib import Path

CONFIG_PATH = Path("/etc/BikeCon/config.json")
SOCKET_PATH = Path("/var/run/BikeCon/mixer.sock")

# Fallback if /var/run is not writable
try:
    os.makedirs("/var/run/BikeCon", exist_ok=True)
except (PermissionError, OSError):
    SOCKET_PATH = Path("/tmp/BikeCon/mixer.sock")
    os.makedirs("/tmp/BikeCon", exist_ok=True)

HID_PATH = Path("/dev/hidg0")

class Mixer:
    def __init__(self):
        self.active_source = "virtual"
        self.bike_active = False
        self.bike_target = "disabled"
        self.bike_max_rpm = 90
        self.current_rpm = 0

        # 加载持久化配置
        self.load_config()

        # [0:LX, 1:LY, 2:RX, 3:RY, 4:LT, 5:RT, 6:BtnLow, 7:BtnHigh]
        # 轴初始化为 128 (中位)，按键初始化为 0
        self.state = [128, 128, 128, 128, 128, 128, 0, 0]
        self.hid_fd = None
        self.connect_hardware()
    
    def load_config(self):
        """从文件读取配置"""
        if CONFIG_PATH.exists():
            try:
                with CONFIG_PATH.open(encoding="utf-8") as f:
                    config = json.load(f)
                    self.bike_target = config.get("target", "disabled")
                    self.bike_max_rpm = config.get("max_rpm", 90)
                    print(f"[Mixer] 配置加载成功: Target={self.bike_target}, MaxRPM={self.bike_max_rpm}")
            except Exception as e:
                print(f"[Mixer] 配置读取失败: {e}")
    
    def save_config(self):
        """保存当前配置到文件"""
        try:
            with CONFIG_PATH.open(encoding="utf-8", mode="w") as f:
                json.dump({"target": self.bike_target, "max_rpm": self.bike_max_rpm}, f)
            print("[Mixer] 配置已持久化")
        except Exception as e:
            print(f"[Mixer] 配置保存失败: {e}")

    def connect_hardware(self):
        try:
            if self.hid_fd:
                try: self.hid_fd.close()
                except: pass
            # Unbuffered mode
            self.hid_fd = open(HID_PATH, 'wb+', buffering=0)
            print(f"[Mixer] >>> 硬件连接建立: {HID_PATH}")
        except Exception as e:
            print(f"[Mixer] !!! 硬件连接失败: {e}")
            self.hid_fd = None
    
    def apply_bike_mapping(self, state_arr):
        """
        根据 RPM 计算映射 (Active 状态下覆盖/叠加虚拟手柄)
        注意：state_arr 传入时包含的是 JoyCon/虚拟手柄 的基础状态
        """
        if not self.bike_active or self.bike_target == "disabled":
            return

        max_rpm = max(1, self.bike_max_rpm)
        # 限制 ratio 在 0.0 - 1.0
        ratio = min(1.0, max(0.0, self.current_rpm / max_rpm))
        
        # 辅助函数：将 ratio 映射到 0-255 或 128-255/0
        def map_axis(val_type):
            if val_type == "normal": return int(128 + (ratio * 127)) # 128 -> 255
            if val_type == "invert": return int(128 - (ratio * 128)) # 128 -> 0
            return 128

        # --- 1. 映射为摇杆 (覆盖模式) ---
        if self.bike_target == "ly":      state_arr[1] = map_axis("normal")
        elif self.bike_target == "ly_inv":state_arr[1] = map_axis("invert")
        elif self.bike_target == "lx":    state_arr[0] = map_axis("normal")
        elif self.bike_target == "lx_inv":state_arr[0] = map_axis("invert")
        elif self.bike_target == "ry":    state_arr[3] = map_axis("normal")
        elif self.bike_target == "ry_inv":state_arr[3] = map_axis("invert")
        elif self.bike_target == "rx":    state_arr[2] = map_axis("normal")
        elif self.bike_target == "rx_inv":state_arr[2] = map_axis("invert")
        
        # --- 2. 映射为扳机 (覆盖模式) ---
        elif self.bike_target == "lt":    state_arr[4] = int(128 + (ratio * 127)) # 128 -> 255
        elif self.bike_target == "rt":    state_arr[5] = int(128 + (ratio * 127)) # 128 -> 255
            
        # --- 3. 映射为按钮 (叠加模式) ---
        # 只有在 RPM 超过阈值时才触发
        elif self.current_rpm >= self.bike_max_rpm:
            # 这里的逻辑是 OR (叠加)，不会覆盖手柄原本的按键
            # Byte 6 (IDX_BTN_L)
            if self.bike_target == "btn_a":      state_arr[6] |= (1 << 0)
            elif self.bike_target == "btn_b":    state_arr[6] |= (1 << 1)
            elif self.bike_target == "btn_x":    state_arr[6] |= (1 << 2)
            elif self.bike_target == "btn_y":    state_arr[6] |= (1 << 3)
            elif self.bike_target == "btn_lb":   state_arr[6] |= (1 << 4)
            elif self.bike_target == "btn_rb":   state_arr[6] |= (1 << 5)
            elif self.bike_target == "btn_select": state_arr[6] |= (1 << 6)
            elif self.bike_target == "btn_start":  state_arr[6] |= (1 << 7)
            
            # Byte 7 (IDX_BTN_H)
            elif self.bike_target == "btn_l3":   state_arr[7] |= (1 << 0)
            elif self.bike_target == "btn_r3":   state_arr[7] |= (1 << 1)
            elif self.bike_target == "btn_up":   state_arr[7] |= (1 << 2)
            elif self.bike_target == "btn_down": state_arr[7] |= (1 << 3)
            elif self.bike_target == "btn_left": state_arr[7] |= (1 << 4)
            elif self.bike_target == "btn_right": state_arr[7] |= (1 << 5)

    def write_hid(self):
        if self.hid_fd is None:
            self.connect_hardware()
            if self.hid_fd is None: return

        try:
            # 复制一份当前状态，避免污染全局状态
            final_state = list(self.state)
            
            # 应用单车映射
            self.apply_bike_mapping(final_state)
            
            # 构造报文: Report ID 0x01 + 8字节数据
            # 必须限制数据在 0-255 范围内，防止 overflow 报错
            packed_data = struct.pack('BBBBBBBB', 
                *[max(0, min(255, val)) for val in final_state]
            )
            
            self.hid_fd.write(b'\x01' + packed_data)
            # self.hid_fd.flush() # Unbuffered 模式下不需要 flush，但加了也无害
        except OSError:
            # 写入失败尝试重连
            self.connect_hardware()
        except Exception as e:
            # print(f"[Mixer] HID Write Error: {e}")
            pass

    async def handle_client(self, reader, writer):
        """处理 Unix Socket 连接"""
        try:
            while True:
                # 按行读取，匹配 bike_service/webapp 发出的 \n
                line = await reader.readline()
                if not line: break # 连接断开
                
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue # 忽略损坏的数据包

                msg_type = msg.get("type")
                        
                # --- 1. 单车状态与数据 ---
                if msg_type == "bike_status":
                    self.bike_active = msg.get("active", False)
                    # print(f"[Mixer] Bike Active: {self.bike_active}")
                    self.write_hid() # 状态改变立即刷新

                elif msg_type == "bike_data":
                    self.current_rpm = int(msg.get("rpm", 0))
                    # 仅在 Active 状态下，RPM 变化才触发 HID 刷新
                    # 避免 Idle 状态下无意义的 IO
                    if self.bike_active and self.bike_target != "disabled":
                        self.write_hid()

                # --- 2. 切换源 ---
                elif msg_type == "source":
                    self.active_source = msg.get("val")
                    # print(f"[Mixer] Source -> {self.active_source}")

                # --- 3. 手柄/Web 输入 (按键) ---
                elif msg_type == "input" or msg_type == "btn":
                    # 只有当前活跃源的输入才生效
                    if msg.get("source") == self.active_source:
                        target = msg.get("target") # "button"
                        if target == "button" or msg_type == "btn":
                            raw_id = int(msg.get("id", 0))
                            btn_id = raw_id - 1 if raw_id > 0 else raw_id
                            
                            is_pressed = int(msg.get("val", 0))
                            
                            # 更新全局状态 state
                            if 0 <= btn_id <= 7:
                                if is_pressed: self.state[6] |= (1 << btn_id)
                                else: self.state[6] &= ~(1 << btn_id)
                            elif 8 <= btn_id <= 15:
                                offset = btn_id - 8
                                if is_pressed: self.state[7] |= (1 << offset)
                                else: self.state[7] &= ~(1 << offset)

                            self.write_hid()

                # --- 4. 手柄/Web 输入 (摇杆) ---
                elif msg_type == "axis":
                    if msg.get("source") == self.active_source:
                        stick = msg.get("stick")
                        x = int(msg.get("x", 128))
                        y = int(msg.get("y", 128))

                        if stick == "left":
                            self.state[0] = x
                            self.state[1] = y
                        elif stick == "right":
                            self.state[2] = x
                            self.state[3] = y
                        
                        self.write_hid()
                
                # --- 5. 手柄/Web 输入 (扳机) ---
                elif msg_type == "trigger":
                    if msg.get("source") == self.active_source:
                        lr = int(msg.get("lr", 0)) # 0:LT, 1:RT
                        val = int(msg.get("val", 0))
                        
                        if lr == 0: self.state[4] = val
                        elif lr == 1: self.state[5] = val
                        
                        self.write_hid()

                # --- 6. 配置更新 ---
                elif msg_type == "bike_config":
                    self.bike_target = msg.get("target")
                    self.bike_max_rpm = int(msg.get("max_rpm", 90))
                    self.save_config()
                    self.write_hid() # 配置改变可能影响输出，立即刷新

        except Exception as e:
            print(f"[Mixer] Client Handler Error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def run(self):
        # 确保 Socket 文件不存在
        if os.path.exists(SOCKET_PATH):
            try: os.remove(SOCKET_PATH)
            except: pass
            
        server = await asyncio.start_unix_server(self.handle_client, path=SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o666)
        print(f"[*] [Mixer] 服务启动: {SOCKET_PATH}")
        
        async with server:
            await server.serve_forever()

if __name__ == "__main__":
    mixer = Mixer()
    try:
        asyncio.run(mixer.run())
    except KeyboardInterrupt:
        pass