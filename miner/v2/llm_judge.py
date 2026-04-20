from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from .types import BoardSnapshot
from .visualization import format_board

DEFAULT_ENDPOINT_CANDIDATES = (
    "http://127.0.0.1:1234/v1/chat/completions",
    "http://100.64.0.7:1234/v1/chat/completions",
    "http://127.0.0.1:8080/v1/chat/completions",
)

DEFAULT_SYSTEM_PROMPT = (
    "You review mushroom mining board snapshots. "
    "Return exactly one JSON object and nothing else. "
    "Do not wrap the JSON in markdown. "
    "You must choose one judgment from: "
    "valid_board, not_mining_screen, overlay_blocked, low_confidence_board, need_retry, need_human_review. "
    "You must choose one next_action from: "
    "continue, retry_screenshot, fallback_classifier_only, human_review. "
    "confidence must be a float between 0 and 1. "
    "reason must be short and concrete. "
    "suspect_cells must be a list of objects with row, col, reason."
)

JUDGE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "mining_v2_board_judge",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "judgment": {
                    "type": "string",
                    "enum": [
                        "valid_board",
                        "not_mining_screen",
                        "overlay_blocked",
                        "low_confidence_board",
                        "need_retry",
                        "need_human_review",
                    ],
                },
                "next_action": {
                    "type": "string",
                    "enum": [
                        "continue",
                        "retry_screenshot",
                        "fallback_classifier_only",
                        "human_review",
                    ],
                },
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
                "suspect_cells": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "row": {"type": "integer"},
                            "col": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                        "required": ["row", "col", "reason"],
                    },
                },
            },
            "required": ["judgment", "next_action", "confidence", "reason", "suspect_cells"],
        },
    },
}


@dataclass
class LlmProbeResult:
    endpoint: str
    models_url: str
    ok: bool
    model_ids: list[str]
    error: Optional[str] = None


@dataclass
class LlmJudgeResult:
    judgment: str
    next_action: str
    confidence: float
    reason: str
    suspect_cells: list[dict[str, Any]]
    endpoint: str
    model: str
    raw_content: str
    parsed_json: dict[str, Any]


class LlmJudgeError(RuntimeError):
    pass


def discover_candidate_endpoints(
    preferred_endpoint: Optional[str] = None,
    config_path: str = "bot_config.json",
) -> list[str]:
    candidates: list[str] = []
    if preferred_endpoint:
        candidates.append(preferred_endpoint)

    config_file = Path(config_path)
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8-sig"))
            endpoint = config.get("global", {}).get("ocr", {}).get("labeler_endpoint")
            if isinstance(endpoint, str) and endpoint.strip():
                candidates.append(endpoint.strip())
        except Exception:
            pass

    for endpoint in DEFAULT_ENDPOINT_CANDIDATES:
        candidates.append(endpoint)

    deduped: list[str] = []
    seen: set[str] = set()
    for endpoint in candidates:
        normalized = endpoint.strip().rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped


def probe_endpoint(endpoint: str, timeout: int = 10) -> LlmProbeResult:
    models_url = build_models_url(endpoint)
    try:
        response = requests.get(models_url, timeout=(5, timeout))
        response.raise_for_status()
        payload = response.json()
        model_ids = [
            str(item.get("id", "")).strip()
            for item in payload.get("data", [])
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]
        return LlmProbeResult(
            endpoint=endpoint,
            models_url=models_url,
            ok=True,
            model_ids=model_ids,
        )
    except Exception as exc:
        return LlmProbeResult(
            endpoint=endpoint,
            models_url=models_url,
            ok=False,
            model_ids=[],
            error=str(exc),
        )


def resolve_endpoint_and_model(
    preferred_endpoint: Optional[str] = None,
    preferred_model: Optional[str] = None,
    timeout: int = 10,
) -> tuple[LlmProbeResult, str]:
    last_probe: Optional[LlmProbeResult] = None
    for endpoint in discover_candidate_endpoints(preferred_endpoint=preferred_endpoint):
        probe = probe_endpoint(endpoint, timeout=timeout)
        last_probe = probe
        if not probe.ok:
            continue
        if preferred_model and preferred_model.strip():
            return probe, preferred_model.strip()
        if probe.model_ids:
            return probe, probe.model_ids[0]
        raise LlmJudgeError(f"endpoint reachable but no models returned: {probe.endpoint}")

    if last_probe is not None:
        raise LlmJudgeError(f"no reachable LLM endpoint; last error: {last_probe.error}")
    raise LlmJudgeError("no reachable LLM endpoint")


def build_models_url(endpoint: str) -> str:
    normalized = endpoint.strip().rstrip("/")
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid endpoint: {endpoint}")
    if parsed.path.endswith("/v1/chat/completions"):
        return normalized[: -len("/chat/completions")] + "/models"
    if parsed.path.endswith("/v1/responses"):
        return normalized[: -len("/responses")] + "/models"
    if parsed.path.endswith("/v1/models"):
        return normalized
    return normalized + "/v1/models"


def build_native_load_url(endpoint: str) -> str:
    normalized = endpoint.strip().rstrip("/")
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid endpoint: {endpoint}")
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/models/load"


def load_model(endpoint: str, model: str, timeout: int = 600) -> dict[str, Any]:
    load_url = build_native_load_url(endpoint)
    response = requests.post(load_url, json={"model": model}, timeout=(10, timeout))
    if response.status_code >= 400:
        body = response.text[:500].replace("\n", " ")
        raise LlmJudgeError(f"HTTP {response.status_code} from {load_url}: {body}")
    return response.json()


def build_snapshot_payload(snapshot: BoardSnapshot, device_id: Optional[str] = None) -> dict[str, Any]:
    rounded_confidences = [
        [round(value, 4) for value in row]
        for row in snapshot.confidences
    ]
    payload: dict[str, Any] = {
        "task": "mining_v2_board_judge",
        "device_id": device_id or "unknown_device",
        "captured_at": snapshot.captured_at,
        "image_shape": [int(value) for value in snapshot.image_shape],
        "grid_config": {key: int(value) for key, value in snapshot.grid_config.items()},
        "board": snapshot.board,
        "board_visual": format_board(snapshot.board),
        "confidences": rounded_confidences,
        "avg_confidence": round(snapshot.avg_confidence, 4),
        "min_confidence": round(snapshot.min_confidence, 4),
    }
    if snapshot.screen_check is not None:
        payload["screen_check"] = {
            "passed": bool(snapshot.screen_check.passed),
            "matched_points": int(snapshot.screen_check.matched_points),
        }
    return payload


def build_user_prompt(snapshot: BoardSnapshot, device_id: Optional[str] = None) -> str:
    payload = build_snapshot_payload(snapshot, device_id=device_id)
    example = {
        "judgment": "low_confidence_board",
        "next_action": "retry_screenshot",
        "confidence": 0.82,
        "reason": "screen check failed and several cells look unreliable",
        "suspect_cells": [{"row": 5, "col": 2, "reason": "low_confidence"}],
    }
    return (
        "/no_think\n"
        "Review this Miner V2 snapshot and decide whether it is safe to continue.\n"
        "Return exactly one JSON object with keys: judgment, next_action, confidence, reason, suspect_cells.\n"
        f"Snapshot:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        f"Example format:\n{json.dumps(example, ensure_ascii=False)}"
    )


def build_chat_payload(
    *,
    model: str,
    snapshot: BoardSnapshot,
    device_id: Optional[str] = None,
    image_path: Optional[str] = None,
    include_image: bool = False,
    max_tokens: int = 500,
) -> dict[str, Any]:
    user_prompt = build_user_prompt(snapshot, device_id=device_id)
    user_content: Any = user_prompt

    if include_image:
        if not image_path:
            raise ValueError("image_path is required when include_image=True")
        user_content = [
            {"type": "text", "text": user_prompt},
            {
                "type": "image_url",
                "image_url": {"url": image_path_to_data_url(Path(image_path))},
            },
        ]

    return {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": JUDGE_RESPONSE_FORMAT,
        "messages": [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }


def judge_snapshot(
    *,
    snapshot: BoardSnapshot,
    endpoint: str,
    model: str,
    device_id: Optional[str] = None,
    image_path: Optional[str] = None,
    include_image: bool = False,
    timeout: int = 120,
    max_tokens: int = 500,
    max_attempts: int = 2,
    auto_load_if_needed: bool = True,
) -> LlmJudgeResult:
    last_error: Optional[Exception] = None
    last_raw_content = ""

    for _ in range(max(1, max_attempts)):
        payload = build_chat_payload(
            model=model,
            snapshot=snapshot,
            device_id=device_id,
            image_path=image_path,
            include_image=include_image,
            max_tokens=max_tokens,
        )
        response = requests.post(endpoint, json=payload, timeout=(10, timeout))
        if response.status_code >= 400:
            body = response.text[:500].replace("\n", " ")
            if auto_load_if_needed and response.status_code == 400 and "Model unloaded." in body:
                load_model(endpoint, model)
                last_error = LlmJudgeError(f"model {model} was unloaded and has been reloaded")
                continue
            raise LlmJudgeError(f"HTTP {response.status_code} from {endpoint}: {body}")

        response_payload = response.json()
        raw_content = extract_content_from_response(response_payload)
        last_raw_content = raw_content
        if not raw_content:
            last_error = LlmJudgeError(f"response missing content from {endpoint}")
            continue

        try:
            parsed = parse_json_response(raw_content)
        except Exception as exc:
            last_error = exc
            continue

        return LlmJudgeResult(
            judgment=str(parsed.get("judgment", "")).strip(),
            next_action=str(parsed.get("next_action", "")).strip(),
            confidence=clamp_confidence(parsed.get("confidence")),
            reason=str(parsed.get("reason", "")).strip(),
            suspect_cells=normalize_suspect_cells(parsed.get("suspect_cells")),
            endpoint=endpoint,
            model=model,
            raw_content=raw_content,
            parsed_json=parsed,
        )

    snippet = last_raw_content[:1200].replace("\n", "\\n")
    if last_error is None:
        raise LlmJudgeError(f"failed to obtain a valid LLM response from {endpoint}")
    raise LlmJudgeError(
        f"failed to parse LLM JSON from {endpoint}: {type(last_error).__name__}: {last_error}; raw={snippet}"
    ) from last_error


def image_path_to_data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime = "image/jpeg"
    if suffix == ".png":
        mime = "image/png"
    elif suffix == ".webp":
        mime = "image/webp"
    with image_path.open("rb") as handle:
        payload = base64.b64encode(handle.read()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def extract_content_from_response(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
                if parts:
                    return "\n".join(parts).strip()
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                return reasoning.strip()

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = data.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text") or block.get("output_text")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
        if parts:
            return "\n".join(parts).strip()

    return ""


def parse_json_response(content: str) -> dict[str, Any]:
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        parsed = extract_json_object_from_text(raw)
        if parsed is None:
            raise
        return parsed


def extract_json_object_from_text(text: str) -> Optional[dict[str, Any]]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def clamp_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def normalize_suspect_cells(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cells: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = item.get("row")
        col = item.get("col")
        reason = str(item.get("reason", "")).strip()
        if isinstance(row, int) and isinstance(col, int):
            cells.append({"row": row, "col": col, "reason": reason})
    return cells
