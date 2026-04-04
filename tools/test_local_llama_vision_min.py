import base64
import json
import re
from pathlib import Path

import requests

# 只要改這三個
ENDPOINT = "http://100.64.0.7:1234/v1/chat/completions"
MODEL = "qwen/qwen3.5-9b"
IMAGE_PATH = r"A:\ocr_fails\now_stage_low_confidence_0.060_1753959214881.jpg"


def main():
    p = Path(IMAGE_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")

    img_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    print(f"Loaded image: {p}, size={p.stat().st_size} bytes, b64_len={len(img_b64)}")
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 120,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "/no_think\n只輸出單行 JSON，不要解釋、不要思考過程、不要 markdown。格式固定為 {\"has_text\": true/false, \"text\": \"...\"}"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}"
                        }
                    }
                ],
            }
        ],
        "response_format": {"type": "text"},
        "stop": [],
    }

    print(f"[SEND] endpoint={ENDPOINT}")
    print(f"[SEND] image={p.name}, bytes={p.stat().st_size}, b64_len={len(img_b64)}")

    r = requests.post(ENDPOINT, json=payload, timeout=(10, 300))
    print(f"[HTTP] status={r.status_code}")
    print("[RAW]")
    print(r.text[:2000])

    if r.status_code == 200:
        data = r.json()
        msg_obj = data["choices"][0]["message"]
        msg = msg_obj.get("content") or ""
        if not msg.strip():
            msg = msg_obj.get("reasoning_content") or ""

        print("[PARSED]")
        print(msg)

        if data["choices"][0].get("finish_reason") == "length":
            print("[WARN] finish_reason=length，回應被截斷，請再降低 max_tokens 或檢查模型是否忽略 no_think")

        json_pattern = r"\{\s*\"has_text\"\s*:\s*(?:true|false)\s*,\s*\"text\"\s*:\s*\"(?:\\.|[^\"\\])*\"\s*\}"
        candidates = re.findall(json_pattern, msg)
        if candidates:
            try:
                parsed = json.loads(candidates[-1])
                print("[JSON]")
                print(parsed)
            except Exception:
                pass


if __name__ == "__main__":
    main()
