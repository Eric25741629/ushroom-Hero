import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import config_manager
from Spin_Wheel import spin_wheel
from device_wrapper import MonitoredDevice, close_all_web_devices, create_web_device_if_enabled

DEFAULT_IP = os.environ.get("SPIN_TEST_IP", "emulator-5554")


def _project_root() -> Path:
    root = Path(__file__).resolve().parent
    os.chdir(root)
    return root


def _open_wheel(target_ip: str, load_model: bool):
    cnn_model = None
    if load_model:
        from utils.model_sync import ensure_local_model
        import new_cnn.cnn_model as cnn_model_module

        cnn_model = cnn_model_module.load_cnn_model(ensure_local_model("cnn_model.pth"))
        print("CNN model loaded", flush=True)

    device_cfg = config_manager.get_device_config(target_ip)
    assert str(device_cfg.get("backend", "adb")).lower() == "web_h5", f"{target_ip} is not a web_h5 device"
    assert device_cfg.get("web_url"), f"{target_ip} has no web_url"

    raw_device = create_web_device_if_enabled(target_ip, cfg=device_cfg)
    monitored = MonitoredDevice(raw_device, target_ip)
    wheel = spin_wheel(device=monitored, cnn_model=cnn_model, devices_serial=target_ip)
    return monitored, wheel, device_cfg


def _resolve_output_path(ip: str, output: str) -> Path:
    out_path = Path(output) if output else Path("debug_img") / f"{ip}_spin_test.png"
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


def _save_screenshot(device, ip: str, output: str) -> dict:
    out_path = _resolve_output_path(ip, output)
    img = device.screenshot(format="opencv")
    ok = cv2.imwrite(str(out_path), img)
    if not ok:
        raise RuntimeError(f"failed to write screenshot to {out_path}")
    return {
        "action": "screenshot",
        "ip": ip,
        "output": str(out_path),
        "shape": list(img.shape),
    }


def cmd_info(args) -> int:
    _project_root()
    cfg = json.loads(Path("bot_config.json").read_text(encoding="utf-8"))
    devices = [k for k, v in cfg.get("devices", {}).items() if str(v.get("backend", "adb")).lower() == "web_h5"]
    print(json.dumps({"project_root": str(Path.cwd()), "web_h5_devices": devices}, ensure_ascii=False, indent=2))
    return 0


def cmd_screenshot(args) -> int:
    _project_root()
    try:
        device, wheel, device_cfg = _open_wheel(args.ip, load_model=args.load_model)
        result = _save_screenshot(device, args.ip, args.output)
        result["web_url"] = device_cfg.get("web_url")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        close_all_web_devices()


def cmd_run(args) -> int:
    _project_root()
    try:
        device, wheel, device_cfg = _open_wheel(args.ip, load_model=args.load_model)
        if args.delay_sec > 0:
            print(f"sleeping {args.delay_sec}s before run", flush=True)
            time.sleep(args.delay_sec)
        result = bool(wheel.spin_and_send_gold())
        print(json.dumps({
            "action": "run",
            "ip": args.ip,
            "web_url": device_cfg.get("web_url"),
            "result": result,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        close_all_web_devices()


def cmd_observe(args) -> int:
    _project_root()
    try:
        device, wheel, device_cfg = _open_wheel(args.ip, load_model=False)
        print(json.dumps({
            "action": "observe",
            "ip": args.ip,
            "web_url": device_cfg.get("web_url"),
            "message": "browser opened; commands: run | screenshot [path] | url | exit",
        }, ensure_ascii=False, indent=2), flush=True)
        while True:
            try:
                command = input("> ").strip()
            except EOFError:
                command = "exit"

            if not command:
                continue
            if command in {"exit", "quit"}:
                print("observe stopped", flush=True)
                return 0
            if command == "run":
                result = bool(wheel.spin_and_send_gold())
                print(json.dumps({
                    "action": "run",
                    "ip": args.ip,
                    "web_url": device_cfg.get("web_url"),
                    "result": result,
                }, ensure_ascii=False, indent=2), flush=True)
                continue
            if command.startswith("screenshot"):
                raw = command[len("screenshot"):].strip()
                # allow users to wrap path in single/double quotes; strip them
                if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
                    raw = raw[1:-1]
                output = raw
                result = _save_screenshot(device, args.ip, output)
                result["web_url"] = device_cfg.get("web_url")
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                continue
            if command == "url":
                current_url = getattr(getattr(device._d, "_page", None), "url", None)
                print(json.dumps({
                    "action": "url",
                    "ip": args.ip,
                    "web_url": device_cfg.get("web_url"),
                    "current_url": current_url,
                }, ensure_ascii=False, indent=2), flush=True)
                continue
            print("unknown command; use: run | screenshot [path] | url | exit", flush=True)
    except KeyboardInterrupt:
        print("observe stopped", flush=True)
        return 0
    finally:
        close_all_web_devices()


def main() -> int:
    parser = argparse.ArgumentParser(description="Single-device runner for spin_and_send_gold web_h5 testing.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    info_parser = subparsers.add_parser("info")
    info_parser.set_defaults(func=cmd_info)

    screenshot_parser = subparsers.add_parser("screenshot")
    screenshot_parser.add_argument("--ip", required=True)
    screenshot_parser.add_argument("--output", default="")
    screenshot_parser.add_argument("--load-model", action="store_true")
    screenshot_parser.set_defaults(func=cmd_screenshot)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--ip", required=True)
    run_parser.add_argument("--load-model", action="store_true")
    run_parser.add_argument("--delay-sec", type=float, default=0.0)
    run_parser.set_defaults(func=cmd_run)

    observe_parser = subparsers.add_parser("observe")
    observe_parser.add_argument("--ip", default=DEFAULT_IP)
    observe_parser.set_defaults(func=cmd_observe)

    if len(sys.argv) == 1:
        args = parser.parse_args(["observe", "--ip", DEFAULT_IP])
    else:
        args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
