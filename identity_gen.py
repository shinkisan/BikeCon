import pyshark
import json
import re
import argparse
import os
import sys

def extract_to_auth_json(file_path):
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
        "client_id": "Unknown", 
        "uuid1": "Unknown", 
        "uuid2": "Unknown"  
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
                
                # 1. 提取 UUID (来自 2f33 包)
                if "a5a5a0" in value_hex and "2f33" in value_hex:
                    try:
                        ascii_str = bytes.fromhex(value_hex).decode('ascii', errors='ignore')
                        
                        u1 = re.search(r'[a-f0-9]{24}', ascii_str)
                        if u1 and auth_data["uuid1"] == "Unknown": 
                            auth_data["uuid1"] = u1.group(0)
                        
                        temp_str = re.sub(r'[a-f0-9]{24}', '', ascii_str)
                        u2 = re.search(r'[a-f0-9]{16}', temp_str)
                        if u2 and auth_data["uuid2"] == "Unknown": 
                            auth_data["uuid2"] = u2.group(0)
                    except:
                        pass
                
                # 2. 提取 Client ID (来自 2f31 包)
                # 修复核心：必须是手机发出的包，且只有当 client_id 为 Unknown 时才写入
                if is_from_phone and auth_data["client_id"] == "Unknown":
                    if "a5a5a0" in value_hex and "2f31" in value_hex:
                        try:
                            # 匹配规律: b3 30/31 2f 31 ff + 32个Hex(即16字节文本) + 00
                            match_client = re.search(r'b33[01]2f31ff([0-9a-f]{32})00', value_hex)
                            if match_client:
                                client_str = bytes.fromhex(match_client.group(1)).decode('ascii', errors='ignore')
                                auth_data["client_id"] = client_str
                        except:
                            pass

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
    output_filename = "identity.json"
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(auth_data, f, indent=4, ensure_ascii=False)
    
    print(f"[√] 解析完成！鉴权配置已保存至: {output_filename}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Keep 单车鉴权信息提取工具")
    parser.add_argument("log", help="原始 .btsnoop 或 .pcap 日志路径")
    args = parser.parse_args()
    
    extract_to_auth_json(args.log)