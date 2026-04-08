import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = {
    "bike_type": "keep",
    "target": "rt",
    "max_rpm": 90,
    "ftms_layer_enabled": False,
    "language": "zh",
}


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def apply_config(config_path: Path, bike_type: str):
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_load_json(config_path))
    cfg["bike_type"] = bike_type

    # FTMS 直连模式下，兼容层强制关闭
    if bike_type == "ftms":
        cfg["ftms_layer_enabled"] = False
    elif "ftms_layer_enabled" not in cfg:
        cfg["ftms_layer_enabled"] = False

    _write_json(config_path, cfg)
    print(f"[√] 配置已更新: {config_path} (bike_type={bike_type})")


def extract_to_auth_json(file_path: str, output_path: Path) -> dict:
    print(f"[*] 正在分析二进制日志: {file_path}")

    if not os.path.exists(file_path):
        print(f"\n[!] 错误：找不到文件 '{file_path}'，请检查路径是否正确。")
        raise SystemExit(1)

    try:
        import pyshark
    except Exception:
        print("\n[!] 缺少 pyshark 依赖，无法解析 Keep HCI 日志。")
        print("[!] 请先安装：pip install pyshark")
        raise SystemExit(1)

    display_filter = '(bthci_acl.src.name contains "Keep" || bthci_acl.dst.name contains "Keep") && btatt'

    auth_data = {
        "bike_name": "Unknown",
        "bike_mac": "Unknown",
        "handshake_packets": [],
    }

    try:
        cap = pyshark.FileCapture(file_path, display_filter=display_filter, keep_packets=False)

        for pkt in cap:
            try:
                src_name = getattr(pkt.bthci_acl, "src_name", "Unknown")
                src_mac = getattr(pkt.bthci_acl, "src_bd_addr", getattr(pkt.bluetooth, "src", "Unknown"))
                dst_name = getattr(pkt.bthci_acl, "dst_name", "Unknown")
                dst_mac = getattr(pkt.bthci_acl, "dst_bd_addr", getattr(pkt.bluetooth, "dst", "Unknown"))

                if "Keep" in src_name:
                    auth_data["bike_name"] = src_name
                    auth_data["bike_mac"] = str(src_mac).upper()
                elif "Keep" in dst_name:
                    auth_data["bike_name"] = dst_name
                    auth_data["bike_mac"] = str(dst_mac).upper()

                value_hex = getattr(pkt.btatt, "value", "").replace(":", "").lower()
                if value_hex.startswith(("a5a5a000", "a5a5a001", "a5a5a002", "a5a5a003")):
                    prefix = value_hex[:8]
                    if not any(pkt_hex.startswith(prefix) for pkt_hex in auth_data["handshake_packets"]):
                        auth_data["handshake_packets"].append(value_hex)
                        auth_data["handshake_packets"].sort(key=lambda x: x[:8])
            except Exception:
                continue

        cap.close()
    except Exception:
        print("\n" + "=" * 60)
        print("[!] 解析失败：文件不符合规范！")
        print("[!] 请确保您提供的是未经修改的原始二进制日志（.btsnoop / .log）。")
        print("[!] 请从安卓设备中重新提取 HCI 日志文件，切勿使用 Wireshark 导出的 txt 文本。")
        print("=" * 60 + "\n")
        raise SystemExit(1)

    if not auth_data.get("bike_mac") or auth_data.get("bike_mac") == "Unknown":
        print("\n[!] 未能从日志中提取 bike_mac，请检查日志是否包含 Keep 连接过程。")
        raise SystemExit(1)

    if not auth_data.get("handshake_packets"):
        print("\n[!] 未提取到握手包 handshake_packets，Keep 模式无法使用。")
        raise SystemExit(1)

    _write_json(output_path, auth_data)
    print(f"[√] Keep 鉴权解析完成，已保存: {output_path}")
    return auth_data


def _is_ftms_uuid(uuid_value: str) -> bool:
    v = str(uuid_value).strip().lower()
    return v == "1826" or v.startswith("00001826")


def _extract_uuids(device: Any) -> list[str]:
    uuids: list[str] = []

    metadata = getattr(device, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("uuids", "UUIDs", "service_uuids", "serviceUUIDs"):
            val = metadata.get(key)
            if isinstance(val, list):
                uuids.extend(str(x) for x in val)

    details = getattr(device, "details", None)
    if isinstance(details, dict):
        props = details.get("props")
        if isinstance(props, dict):
            val = props.get("UUIDs")
            if isinstance(val, list):
                uuids.extend(str(x) for x in val)

    return list(dict.fromkeys(uuids))


async def scan_ble_devices(timeout: float) -> list[dict]:
    try:
        from bleak import BleakScanner
    except Exception:
        print("\n[!] 缺少 bleak 依赖，无法扫描 FTMS 设备。")
        print("[!] 请先安装：pip install bleak")
        raise SystemExit(1)

    try:
        devices = await BleakScanner.discover(timeout=timeout)
    except Exception as e:
        print(f"\n[!] BLE 扫描失败: {e}")
        raise SystemExit(1)

    results: list[dict] = []
    for dev in devices:
        name = getattr(dev, "name", None)
        addr = getattr(dev, "address", None)
        if not addr:
            continue

        uuids = _extract_uuids(dev)
        is_ftms = any(_is_ftms_uuid(u) for u in uuids)

        results.append({
            "name": str(name) if name else "",
            "address": str(addr),
            "rssi": getattr(dev, "rssi", None),
            "uuids": uuids,
            "is_ftms": is_ftms,
        })

    results.sort(key=lambda d: (not d["is_ftms"], d["name"].lower(), d["address"].lower()))
    return results


def _confirm_enter_or_esc() -> bool:
    msg = "[*] 未提供日志参数，将进入 FTMS 扫描模式。按 Enter 继续，按 Esc 退出。"
    print(msg)

    if not sys.stdin.isatty():
        ans = input("按 Enter 继续，输入 ESC 后回车退出: ")
        return ans != "\x1b"

    try:
        import tty
        import termios
    except Exception:
        ans = input("按 Enter 继续，输入 ESC 后回车退出: ")
        return ans != "\x1b"

    fd = sys.stdin.fileno()
    while True:
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

        if ch in ("\r", "\n"):
            print("")
            return True
        if ch == "\x1b":
            print("")
            return False


def _print_device_list(devices: list[dict]):
    print("\n发现以下 BLE 设备：")
    print("------------------------------------------------------------")
    for idx, dev in enumerate(devices, start=1):
        tag = "FTMS" if dev["is_ftms"] else "BLE "
        name = dev["name"] if dev["name"] else "(no name)"
        rssi = dev["rssi"] if dev["rssi"] is not None else "?"
        print(f"[{idx:02d}] [{tag}] {name} | {dev['address']} | RSSI={rssi}")
    print("------------------------------------------------------------")


def _select_device_interactive(devices: list[dict]) -> dict | None:
    while True:
        ans = input("请选择设备编号（输入 r 重扫，q 退出）: ").strip().lower()
        if ans in ("q", "quit", "exit"):
            return None
        if ans in ("r", "rescan"):
            return {"_rescan": True}
        if ans.isdigit():
            idx = int(ans)
            if 1 <= idx <= len(devices):
                return devices[idx - 1]
        print("[!] 输入无效，请重新输入。")


async def generate_ftms_identity(output_path: Path, scan_timeout: float):
    while True:
        print(f"\n[*] 正在扫描 BLE 设备（{scan_timeout:.1f}s）...")
        devices = await scan_ble_devices(scan_timeout)

        if not devices:
            print("[!] 未发现任何 BLE 设备。")
            ans = input("输入 r 重扫，q 退出: ").strip().lower()
            if ans in ("q", "quit", "exit"):
                raise SystemExit(0)
            continue

        if not any(d["is_ftms"] for d in devices):
            print("[!] 未识别到明确 FTMS 广播，将显示全部 BLE 设备供你选择。")

        _print_device_list(devices)
        selected = _select_device_interactive(devices)
        if selected is None:
            raise SystemExit(0)
        if isinstance(selected, dict) and selected.get("_rescan"):
            continue

        identity = {
            "bike_name": selected["name"] if selected["name"] else "FTMS_Device",
            "bike_mac": selected["address"],
            "protocol": "ftms",
        }
        _write_json(output_path, identity)
        print(f"[√] FTMS identity 已保存: {output_path}")
        print(f"    设备: {identity['bike_name']} ({identity['bike_mac']})")
        return identity


def main():
    parser = argparse.ArgumentParser(description="BikeCon 身份配置生成工具（Keep/FTMS 双模式）")
    parser.add_argument("log", nargs="?", help="Keep 模式日志路径（传入则按 Keep 流程解析）")
    parser.add_argument("-o", "--output", default=None, help="输出 identity.json 路径（默认脚本同目录）")
    parser.add_argument("-c", "--config", default=None, help="要更新的 config.json 路径（默认脚本同目录）")
    parser.add_argument("--scan-timeout", type=float, default=8.0, help="FTMS 扫描时长（秒）")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    output_path = Path(args.output).expanduser().resolve() if args.output else (script_dir / "identity.json")
    config_path = Path(args.config).expanduser().resolve() if args.config else (script_dir / "config.json")

    if args.log:
        extract_to_auth_json(args.log, output_path)
        apply_config(config_path, "keep")
        return

    if not _confirm_enter_or_esc():
        print("[*] 已取消。")
        raise SystemExit(0)

    asyncio.run(generate_ftms_identity(output_path, args.scan_timeout))
    apply_config(config_path, "ftms")


if __name__ == "__main__":
    main()
