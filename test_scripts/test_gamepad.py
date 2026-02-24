import time
import struct
import os

def main_loop():
    report_path = '/dev/hidg0'
    print(f"等待设备 {report_path}...")
    
    # 等待文件出现
    while not os.path.exists(report_path):
        time.sleep(1)
        
    print("连接成功！开始发送 6 字节数据包...")
    
    try:
        with open(report_path, 'wb+') as fd:
            while True:
                # --- 数据包结构 (6字节) ---
                # Byte 0: Left X  (128=中)
                # Byte 1: Left Y  (128=中)
                # Byte 2: Right X (128=中)
                # Byte 3: Right Y (128=中)
                # Byte 4: Buttons Low (8位)
                # Byte 5: Buttons High (8位)
                
                # 动作 1：按下 A 键 (Button 1)
                # Buttons Low = 0x01 (0000 0001)
                packet_press = struct.pack('BBBBBB', 128, 128, 128, 128, 0x01, 0x00)
                fd.write(packet_press)
                fd.flush()
                print("Press A") 
                time.sleep(0.5)
                
                # 动作 2：按下 B 键 (Button 2)
                # Buttons Low = 0x02 (0000 0010)
                packet_press_b = struct.pack('BBBBBB', 128, 128, 128, 128, 0x02, 0x00)
                fd.write(packet_press_b)
                fd.flush()
                print("Press B")
                time.sleep(0.5)

                # 动作 3：松开所有
                packet_release = struct.pack('BBBBBB', 128, 128, 128, 128, 0x00, 0x00)
                fd.write(packet_release)
                fd.flush()
                print("Release")
                time.sleep(0.5)
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main_loop()