import argparse
import ast
import base64
import json
import os
import shutil
import sys
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


@dataclass
class MaxFailConfig:
    env_key: str
    default_value: int


def _log(log_fn, msg: str) -> None:
    if callable(log_fn):
        log_fn(msg)
    else:
        print(msg)


def normalize_cli_path(path_str: str) -> Path:
    # Keep drive-letter style path (e.g. A:\...) and avoid Path.resolve()
    # because resolve() on mapped drives may expand to UNC.
    return Path(os.path.abspath(os.path.expanduser(path_str)))


def read_max_fail_config(ocr_server_path: Path) -> MaxFailConfig:
    source = ocr_server_path.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "MAX_OCR_FAIL_IMAGES":
                value = node.value
                if not (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "int"
                    and value.args
                ):
                    break
                inner = value.args[0]
                if not (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "get"
                    and len(inner.args) >= 2
                    and isinstance(inner.args[0], ast.Constant)
                    and isinstance(inner.args[1], ast.Constant)
                ):
                    break
                env_key = str(inner.args[0].value)
                default_value = int(inner.args[1].value)
                return MaxFailConfig(env_key=env_key, default_value=default_value)
    raise RuntimeError(f"Cannot parse MAX_OCR_FAIL_IMAGES from: {ocr_server_path}")


def image_to_data_url(image_path: Path) -> str:
    mime = "image/jpeg"
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        mime = "image/png"
    elif suffix == ".webp":
        mime = "image/webp"
    with image_path.open("rb") as f:
        payload = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def parse_json_response(content: str) -> dict:
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def extract_json_object_from_text(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def query_llama_has_text(
    *,
    image_path: Path,
    endpoint: str,
    model: str,
    timeout: int,
    max_retries: int = 2,
    retry_delay_sec: float = 1.5,
    log_fn=None,
) -> tuple[bool, str]:
    prompt = (
        "請判斷圖片是否有可辨識文字。"
        "只回傳 JSON："
        '{"has_text": true/false, "text": "有文字請填內容，沒有請回空字串"}'
    )
    img_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    data_url = f"data:image/jpeg;base64,{img_b64}"
    payload_variants = [
        # OpenAI-style multimodal payload (works on LM Studio / OpenAI-compatible servers)
        {
            "model": model,
            "temperature": 0,
            "max_tokens": 120,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "/no_think\n" + prompt,
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                }
            ],
            "response_format": {"type": "text"},
            "stop": [],
        },
        # llama.cpp native image_data payload fallback
        {
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": f"{prompt}\n[img-1]"}],
            "image_data": [{"id": 1, "data": img_b64}],
        },
    ]

    last_err = None
    for attempt in range(max_retries + 1):
        for variant_idx, payload in enumerate(payload_variants, 1):
            try:
                resp = requests.post(endpoint, json=payload, timeout=(10, timeout))
                if resp.status_code >= 400:
                    body = resp.text[:300].replace("\n", " ")
                    raise RuntimeError(f"variant#{variant_idx} HTTP {resp.status_code}: {body}")

                data = resp.json()
                msg_obj = data["choices"][0]["message"]
                content = (msg_obj.get("content") or "").strip()
                if not content:
                    content = (msg_obj.get("reasoning_content") or "").strip()

                try:
                    parsed = parse_json_response(content)
                except Exception:
                    parsed = extract_json_object_from_text(content)
                    if parsed is None:
                        raise

                has_text = bool(parsed.get("has_text", False))
                text = str(parsed.get("text", "")).strip()
                if has_text and not text:
                    text = "<EMPTY_TEXT>"
                return has_text, text
            except Exception as exc:
                last_err = exc

        if attempt < max_retries:
            _log(
                log_fn,
                f"[RETRY] {image_path.name} attempt {attempt + 1}/{max_retries} "
                f"after error: {type(last_err).__name__}: {last_err}",
            )
            time.sleep(max(0.0, retry_delay_sec))
        else:
            raise RuntimeError(str(last_err))

    raise RuntimeError(str(last_err))


def iter_images(source_dir: Path):
    for p in sorted(source_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            yield p


def sync_fail_count_file(source_dir: Path, log_fn=None) -> int:
    count = 0
    for p in source_dir.iterdir():
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES:
            count += 1
    count_file = source_dir / "count.txt"
    count_file.write_text(str(count), encoding="utf-8")
    _log(log_fn, f"[COUNT] synced {count_file} = {count}")
    return count


def write_label_file(txt_path: Path, text: str) -> None:
    txt_path.write_text(text + "\n", encoding="utf-8")


def ensure_control_dir(control_dir: Path) -> None:
    control_dir.mkdir(parents=True, exist_ok=True)


def is_stop_requested(control_dir: Path) -> bool:
    return (control_dir / "stop.flag").exists()


def wait_if_paused(control_dir: Path, log_fn=None) -> bool:
    pause_flag = control_dir / "pause.flag"
    paused_logged = False
    while pause_flag.exists():
        if is_stop_requested(control_dir):
            return False
        if not paused_logged:
            _log(log_fn, "[PAUSE] paused by control flag")
            paused_logged = True
        time.sleep(1.0)
    if paused_logged:
        _log(log_fn, "[RESUME] resumed")
    return True


def process_once(args) -> None:
    log_fn = getattr(args, "log_fn", None)
    ocr_server_path = normalize_cli_path(args.ocr_server)
    cfg = read_max_fail_config(ocr_server_path)
    max_images = int(os.environ.get(cfg.env_key, str(cfg.default_value)))
    worker_count = max(1, int(getattr(args, "workers", 4) or 4))

    source_dir = normalize_cli_path(args.source_dir)
    output_dir = normalize_cli_path(args.output_dir)
    control_dir = normalize_cli_path(args.control_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_control_dir(control_dir)

    if not source_dir.exists():
        _log(log_fn, f"[WARN] source folder not found: {source_dir}")
        return

    all_images = list(iter_images(source_dir))
    to_process = all_images[:max_images]
    _log(
        log_fn,
        f"[INFO] source={source_dir} total={len(all_images)} limit={max_images} "
        f"processing={len(to_process)} workers={worker_count}",
    )

    kept = 0
    deleted = 0
    errors = 0
    counters_lock = threading.Lock()
    progress_lock = threading.Lock()
    next_index = 0

    def _pop_next_image() -> tuple[int, Path] | None:
        nonlocal next_index
        with progress_lock:
            if next_index >= len(to_process):
                return None
            idx = next_index
            next_index += 1
            return idx, to_process[idx]

    def _mark_result(kind: str) -> None:
        nonlocal kept, deleted, errors
        with counters_lock:
            if kind == "keep":
                kept += 1
            elif kind == "drop":
                deleted += 1
            else:
                errors += 1

    def _worker(worker_id: int) -> None:
        while True:
            if is_stop_requested(control_dir):
                _log(log_fn, f"[STOP] worker#{worker_id} stop requested, terminating current run")
                return
            if not wait_if_paused(control_dir, log_fn=log_fn):
                _log(log_fn, f"[STOP] worker#{worker_id} stop requested while paused")
                return

            item = _pop_next_image()
            if item is None:
                return

            idx, image_path = item
            try:
                _log(log_fn, f"[STEP] {idx + 1}/{len(to_process)} {image_path.name} (worker#{worker_id})")
                has_text, text = query_llama_has_text(
                    image_path=image_path,
                    endpoint=args.endpoint,
                    model=args.model,
                    timeout=args.timeout,
                    max_retries=args.max_retries,
                    retry_delay_sec=args.retry_delay_sec,
                    log_fn=log_fn,
                )
                if has_text:
                    target_img = output_dir / image_path.name
                    target_txt = output_dir / f"{image_path.stem}.txt"
                    shutil.move(str(image_path), str(target_img))
                    write_label_file(target_txt, text)
                    _mark_result("keep")
                    _log(log_fn, f"[KEEP] {target_img.name} -> {target_txt.name} text={text[:80]}")
                else:
                    image_path.unlink(missing_ok=True)
                    _mark_result("drop")
                    _log(log_fn, f"[DROP] {image_path.name} (no text)")
            except Exception as exc:
                _mark_result("error")
                _log(log_fn, f"[ERROR] {image_path.name}: {exc}")

    threads = [threading.Thread(target=_worker, args=(i + 1,), daemon=True, name=f"labeler-worker-{i+1}") for i in range(worker_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    remaining = sync_fail_count_file(source_dir, log_fn=log_fn)
    _log(log_fn, f"[DONE] kept={kept} deleted={deleted} errors={errors} remaining={remaining}")


def next_run_seconds(daily_time: str) -> float:
    hh, mm = daily_time.split(":")
    now = datetime.now()
    target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


def run_forever(args) -> None:
    log_fn = getattr(args, "log_fn", None)
    _log(log_fn, f"[SCHEDULE] daily at {args.daily_time}")
    while True:
        wait_s = next_run_seconds(args.daily_time)
        _log(log_fn, f"[SLEEP] next run in {int(wait_s)} seconds")
        time.sleep(max(1, int(wait_s)))
        process_once(args)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto-label OCR fail images via llama.cpp vision endpoint.")
    parser.add_argument("--ocr-server", default="ocr_server.py", help="Path to ocr_server.py for MAX_OCR_FAIL_IMAGES.")
    parser.add_argument("--source-dir", default="ocr_fails_new", help="Input image folder.")
    parser.add_argument("--output-dir", default="OCR_train", help="Output folder.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/chat/completions", help="llama.cpp endpoint.")
    parser.add_argument("--model", default="local-model", help="Model name for llama.cpp endpoint.")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout seconds.")
    parser.add_argument("--max-retries", type=int, default=2, help="Retry count for each image on request error.")
    parser.add_argument("--retry-delay-sec", type=float, default=1.5, help="Delay between retries.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent worker count for labeling.")
    parser.add_argument("--control-dir", default=".runtime/labeler_control", help="Pause/stop control directory.")
    parser.add_argument("--once", action="store_true", help="Run once immediately.")
    parser.add_argument("--daily-time", default="02:00", help="Run time in HH:MM for scheduler mode.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        if args.once:
            process_once(args)
        else:
            run_forever(args)
        return 0
    except KeyboardInterrupt:
        _log(getattr(args, "log_fn", None), "\n[STOP] interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
