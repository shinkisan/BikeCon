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

    # Disable compatibility layer for FTMS direct mode
    if bike_type == "ftms":
        cfg["ftms_layer_enabled"] = False
    elif "ftms_layer_enabled" not in cfg:
        cfg["ftms_layer_enabled"] = False

    _write_json(config_path, cfg)
    print(f"[√] Configuration updated: {config_path} (bike_type={bike_type})")


def extract_to_auth_json(file_path: str, output_path: Path) -> dict:
    print(f"[*] Analyzing binary log: {file_path}")

    if not os.path.exists(file_path):
        print(f"\n[!] Error: file not found: '{file_path}'. Please check the path.")
        raise SystemExit(1)

    try:
        import pyshark
    except Exception:
        print("\n[!] Missing dependency: pyshark is required to parse Keep HCI logs.")
        print("[!] Install it with: pip install pyshark")
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
        print("[!] Parse failed: file does not appear to be a valid HCI binary log.")
        print("[!] Please provide an unmodified binary HCI log (.btsnoop / .log).")
        print("[!] Extract the HCI log from the Android device; do not use Wireshark-exported text files.")
        print("=" * 60 + "\n")
        raise SystemExit(1)

    if not auth_data.get("bike_mac") or auth_data.get("bike_mac") == "Unknown":
        print("\n[!] Failed to extract bike_mac from log; ensure the log contains the Keep pairing sequence.")
        raise SystemExit(1)

    if not auth_data.get("handshake_packets"):
        print("\n[!] No handshake packets found; Keep mode cannot be used.")
        raise SystemExit(1)

    _write_json(output_path, auth_data)
    print(f"[√] Keep auth data extracted and saved: {output_path}")
    return auth_data


def _is_ftms_uuid(uuid_value: str) -> bool:
    v = str(uuid_value).strip().lower()
    return v == "1826" or v.startswith("00001826")

async def scan_ble_devices(timeout: float) -> list[dict]:
    try:
        from bleak import BleakScanner
    except Exception:
        print("\n[!] Missing dependency: bleak is required for BLE scanning.")
        print("[!] Install it with: pip install bleak")
        raise SystemExit(1)

    # 1. 创建异步扫描任务 (开启 return_adv=True 以消除警告)
    # 注意：这里我们不直接 await，而是先创建 task
    scan_task = asyncio.create_task(BleakScanner.discover(timeout=timeout, return_adv=True))

    # 2. 倒计时逻辑
    remaining = int(timeout)
    while not scan_task.done() and remaining > 0:
        print(f"\rScanning for BLE devices... {remaining:2d}s remaining ", end="", flush=True)
        await asyncio.sleep(1)
        remaining -= 1
    print(f"\rScanning for BLE devices... Complete!          ") # 清理一下行

    # 3. 等待任务彻底完成并获取结果
    try:
        devices_dict = await scan_task
    except Exception as e:
        print(f"\n[!] BLE scan failed: {e}")
        raise SystemExit(1)

    results: list[dict] = []

    # 4. 遍历结果 (使用新的 AdvertisementData 接口)
    for addr, (dev, adv) in devices_dict.items():
        name = adv.local_name or dev.name
        if not addr:
            continue

        # 从 adv 中提取服务 UUIDs
        uuids = [str(u).lower() for u in adv.service_uuids]
        is_ftms = any(_is_ftms_uuid(u) for u in uuids)

        results.append({
            "name": str(name) if name else "",
            "address": str(addr),
            "rssi": adv.rssi,  # 从广播数据获取 RSSI，不再有警告
            "uuids": uuids,
            "is_ftms": is_ftms,
        })

    # 排序：FTMS 设备靠前
    results.sort(key=lambda d: (not d["is_ftms"], d["name"].lower(), d["address"].lower()))
    return results


def _confirm_enter_or_esc() -> bool:
    msg = "[*] No log file provided; entering FTMS scan mode. Press Enter to continue, Esc to cancel."
    print(msg)

    if not sys.stdin.isatty():
        ans = input("Press Enter to continue, or type ESC then Enter to exit: ")
        return ans != "\x1b"

    try:
        import tty
        import termios
    except Exception:
        ans = input("Press Enter to continue, or type ESC then Enter to exit: ")
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
    print("\nDiscovered BLE devices:")
    print("------------------------------------------------------------")
    for idx, dev in enumerate(devices, start=1):
        tag = "FTMS" if dev["is_ftms"] else "BLE "
        name = dev["name"] if dev["name"] else "(no name)"
        rssi = dev["rssi"] if dev["rssi"] is not None else "?"
        print(f"[{idx:02d}] [{tag}] {name} | {dev['address']} | RSSI={rssi}")
    print("------------------------------------------------------------")


def _select_device_interactive(devices: list[dict]) -> dict | None:
    while True:
        ans = input("Select device number (r=rescan, q=quit): ").strip().lower()
        if ans in ("q", "quit", "exit"):
            return None
        if ans in ("r", "rescan"):
            return {"_rescan": True}
        if ans.isdigit():
            idx = int(ans)
            if 1 <= idx <= len(devices):
                return devices[idx - 1]
        print("[!] Invalid input, please try again.")


async def generate_ftms_identity(output_path: Path, scan_timeout: float):
    while True:
        print(f"\n[*] Scanning for BLE devices ({scan_timeout:.1f}s)...")
        devices = await scan_ble_devices(scan_timeout)

        if not devices:
            print("[!] No BLE devices found.")
            ans = input("Enter r to rescan, q to quit: ").strip().lower()
            if ans in ("q", "quit", "exit"):
                raise SystemExit(0)
            continue

        if not any(d["is_ftms"] for d in devices):
            print("[!] No explicit FTMS advertisement found; showing all BLE devices.")

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
        print(f"[√] FTMS identity saved: {output_path}")
        print(f"    Device: {identity['bike_name']} ({identity['bike_mac']})")
        return identity


def main():
    parser = argparse.ArgumentParser(description="BikeCon identity generation tool (Keep/FTMS)")
    parser.add_argument("log", nargs="?", help="Path to Keep mode log (if provided, parse Keep flow)")
    parser.add_argument("-o", "--output", default=None, help="Output identity.json path (default: script directory)")
    parser.add_argument("-c", "--config", default=None, help="Config.json path to update (default: script directory)")
    parser.add_argument("--scan-timeout", type=float, default=8.0, help="FTMS scan timeout in seconds")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    output_path = Path(args.output).expanduser().resolve() if args.output else (script_dir / "identity.json")
    config_path = Path(args.config).expanduser().resolve() if args.config else (script_dir / "config.json")

    if args.log:
        extract_to_auth_json(args.log, output_path)
        apply_config(config_path, "keep")
        return

    if not _confirm_enter_or_esc():
        print("[*] Cancelled.")
        raise SystemExit(0)

    asyncio.run(generate_ftms_identity(output_path, args.scan_timeout))
    apply_config(config_path, "ftms")


if __name__ == "__main__":
    main()
