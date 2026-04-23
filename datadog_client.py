import os
import socket
import time
import uuid
import hashlib
from typing import Dict, Any, Optional, Sequence, List

import requests

from ..config import settings

DATADOG_API_KEY = os.getenv("DATADOG_API_KEY")
DD_SITE = os.getenv("DD_SITE", "us5.datadoghq.com")
SERVICE_NAME = os.getenv("DD_SERVICE", "llm-health-guardian-api")
ENV = os.getenv("DD_ENV", settings.env or "local")

# Logs intake (v2)
LOGS_URL = f"https://http-intake.logs.{DD_SITE}/api/v2/logs"

# Metrics intake (v1 series) - stable schema
METRICS_URL = f"https://api.{DD_SITE}/api/v1/series"

print(
    f"[DD DEBUG] datadog_client loaded. "
    f"API_KEY_present={bool(DATADOG_API_KEY)} site={DD_SITE} env={ENV} service={SERVICE_NAME}"
)


def _now_ts() -> int:
    return int(time.time())


def _hash_user_id(raw_id: Optional[str]) -> Optional[str]:
    if not raw_id:
        return None
    return hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]


def _common_tags(extra: Optional[Sequence[str]] = None) -> List[str]:
    base: List[str] = [
        f"service:{SERVICE_NAME}",
        f"env:{ENV}",
        f"model:{settings.gemini_model_name}",
    ]
    if extra:
        base.extend(list(extra))
    return base


def send_log(event: Dict[str, Any]) -> None:
    """
    Send ONE structured log event to Datadog logs intake.
    Datadog expects a LIST of events.
    """
    if not DATADOG_API_KEY:
        print("[DD DEBUG] send_log called but DATADOG_API_KEY is missing")
        return

    headers = {
        "DD-API-KEY": DATADOG_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(LOGS_URL, headers=headers, json=[event], timeout=5.0)
        print(f"[DD DEBUG] send_log status={resp.status_code} body={resp.text[:200]!r}")
    except Exception as e:
        print(f"[DD ERROR] send_log exception: {e}")


def send_metrics(series: List[Dict[str, Any]]) -> None:
    """
    Send metrics using Datadog v1 series API.

    v1 expects:
      {"series": [{"metric": "...", "points": [[ts, value]], "tags": [...], "type": "gauge|count"}]}
    """
    if not DATADOG_API_KEY:
        print("[DD DEBUG] send_metrics called but DATADOG_API_KEY is missing")
        return

    if not series:
        return

    headers = {
        "DD-API-KEY": DATADOG_API_KEY,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(METRICS_URL, headers=headers, json={"series": series}, timeout=5.0)
        print(f"[DD DEBUG] send_metrics status={resp.status_code} body={resp.text[:200]!r}")
    except Exception as e:
        print(f"[DD ERROR] send_metrics exception: {e}")


def record_chat_observation(
    *,
    prompt: str,
    answer: Optional[str],
    model: str,
    latency_ms: float,
    tokens_input: int,
    tokens_output: int,
    total_tokens: int,
    cost_usd: float,
    status: str,  # "success" | "error"
    error_type: Optional[str] = None,
    user_id: Optional[str] = None,
    safety_blocked: bool = False,
    extra_context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Enriched telemetry for ONE /api/chat request:
      - One structured log event (nested llm object)
      - Multiple metrics (gauges + counters)
    """
    if not DATADOG_API_KEY:
        print("[DD DEBUG] DATADOG_API_KEY missing, skipping send")
        return

    now = _now_ts()
    request_id = str(uuid.uuid4())
    user_id_hash = _hash_user_id(user_id)

    # ---------------- 1) STRUCTURED LOG ----------------
    log_event: Dict[str, Any] = {
        "ddsource": "llm_app",
        "service": SERVICE_NAME,
        "env": ENV,
        "hostname": os.getenv("HOSTNAME", socket.gethostname()),
        "message": "llm_chat_completion",
        "request_id": request_id,
        "llm": {
            "request_id": request_id,
            "user_id_hash": user_id_hash,
            "model": model,
            "prompt_length_chars": len(prompt or ""),
            "response_length_chars": len(answer or ""),
            "latency_ms": latency_ms,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "status": status,  # "success" | "error"
            "error_type": error_type,
            "safety_blocked": safety_blocked,
            "env": ENV,
            "route": "/api/chat",
        },
        "http": {
            "route": "/api/chat",
            "method": "POST",
        },
        "user": {
            "id_hash": user_id_hash,
        },
    }

    if extra_context:
        log_event["context"] = extra_context

    print("[DD DEBUG] record_chat_observation called")
    send_log(log_event)

    # ---------------- 2) METRICS (v1 series) ----------------
    tags = [
        f"service:{SERVICE_NAME}",
        f"env:{ENV}",
        f"model:{model}",
        f"status:{status}",
    ]

    series: List[Dict[str, Any]] = [
        {
            "metric": "llm.health.latency_ms",
            "type": "gauge",
            "points": [[now, float(latency_ms)]],
            "tags": tags,
        },
        {
            "metric": "llm.health.tokens_input",
            "type": "gauge",
            "points": [[now, float(tokens_input)]],
            "tags": tags,
        },
        {
            "metric": "llm.health.tokens_output",
            "type": "gauge",
            "points": [[now, float(tokens_output)]],
            "tags": tags,
        },
        {
            "metric": "llm.health.total_tokens",
            "type": "gauge",
            "points": [[now, float(total_tokens)]],
            "tags": tags,
        },
        {
            "metric": "llm.health.cost_usd",
            "type": "gauge",
            "points": [[now, float(cost_usd)]],
            "tags": tags,
        },
    ]

    # success / error counters
    if status == "success":
        series.append(
            {
                "metric": "llm.health.success",
                "type": "count",
                "points": [[now, 1]],
                "tags": tags,
            }
        )
    else:
        series.append(
            {
                "metric": "llm.health.error",
                "type": "count",
                "points": [[now, 1]],
                "tags": tags,
            }
        )

    if safety_blocked:
        series.append(
            {
                "metric": "llm.health.safety_blocked",
                "type": "count",
                "points": [[now, 1]],
                "tags": tags,
            }
        )

    send_metrics(series)
