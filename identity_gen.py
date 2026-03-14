import pyshark
import json
import re
import argparse
import os
import sys

def extract_to_auth_json(file_path, output_filename="identity.json"):
    print(f"[*] 正在分析二进制日志: {file_path}")
    
    # --- 校验文件是否存在 ---
    if not os.path.exists(file_path):
        print(f"\n[!] 错误：找不到文件 '{file_path}'，请检查路径是否正确。")
        sys.exit(1)

    display_filter = '(bthci_acl.src.name contains "Keep" || bthci_acl.dst.name contains "Keep") && btatt'
    
    # 把 phone_mac 替换成 client_id
    auth_data = {
        "bike_name": "Unknown",
        "bike_mac": "Unknown",
        "handshake_packets": []  # 新增握手包存储
    }

    # --- 在外层添加 try-except 拦截解析引擎崩溃 ---
    try:
        cap = pyshark.FileCapture(file_path, display_filter=display_filter, keep_packets=False)
        
        # 当遍历 cap 时，pyshark 才会真正驱动 tshark 去读取文件
        for pkt in cap:
            try:
                src_name = getattr(pkt.bthci_acl, 'src_name', 'Unknown')
                src_mac = getattr(pkt.bthci_acl, 'src_bd_addr', getattr(pkt.bluetooth, 'src', 'Unknown'))
                dst_name = getattr(pkt.bthci_acl, 'dst_name', 'Unknown')
                dst_mac = getattr(pkt.bthci_acl, 'dst_bd_addr', getattr(pkt.bluetooth, 'dst', 'Unknown'))

                # 增加方向判断标志
                is_from_phone = False

                if "Keep" in src_name:
                    auth_data["bike_name"] = src_name
                    auth_data["bike_mac"] = src_mac.upper()
                elif "Keep" in dst_name:
                    auth_data["bike_name"] = dst_name
                    auth_data["bike_mac"] = dst_mac.upper()
                    is_from_phone = True  # 目的地是单车，说明是手机发出的请求

                value_hex = getattr(pkt.btatt, 'value', '').replace(':', '').lower()
                
                # 提取握手包 (前缀 a5a5a000 ~ a5a5a003)
                if value_hex.startswith(('a5a5a000', 'a5a5a001', 'a5a5a002', 'a5a5a003')):
                    # 只保留前4个不同前缀的包
                    current_prefix = value_hex[:8]
                    if not any(packet.startswith(current_prefix) for packet in auth_data["handshake_packets"]):
                        auth_data["handshake_packets"].append(value_hex)
                        # 按前缀排序
                        auth_data["handshake_packets"].sort(key=lambda x: x[:8])
                        
            except Exception:
                continue
                
        cap.close()

    # --- 捕获格式错误并给出提示 ---
    except Exception as e:
        print("\n" + "="*60)
        print("[!] 解析失败：文件不符合规范！")
        print("[!] 请确保您提供的是未经修改的原始二进制日志（.btsnoop / .log）。")
        print("[!] 请从安卓设备中重新提取 HCI 日志文件，切勿使用 Wireshark 导出的 txt 文本。")
        print("="*60 + "\n")
        sys.exit(1)

    # 写入 JSON 文件
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(auth_data, f, indent=4, ensure_ascii=False)
    
    print(f"[√] 解析完成！鉴权配置已保存至: {output_filename}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Keep 单车鉴权信息提取工具")
    parser.add_argument("log", help="原始 .btsnoop 或 .pcap 日志路径")
    parser.add_argument("-o", "--output", default="identity.json", help="输出 JSON 文件名")
    args = parser.parse_args()
    
    extract_to_auth_json(args.log, args.output)