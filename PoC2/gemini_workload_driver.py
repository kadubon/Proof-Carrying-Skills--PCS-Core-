from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
except Exception:  # pragma: no cover
    genai = None
    genai_errors = None
    genai_types = None

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def canonical_json_hash(obj: Any) -> str:
    encoded = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(encoded)


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _extract_gemini_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    workload = cfg.get("workload")
    if isinstance(workload, dict):
        gemini_cfg = workload.get("gemini")
        if isinstance(gemini_cfg, dict):
            return gemini_cfg
    gemini_cfg = cfg.get("gemini")
    if isinstance(gemini_cfg, dict):
        return gemini_cfg
    return {}


def _runtime_dry_run(cfg: dict[str, Any], gemini_cfg: dict[str, Any]) -> bool:
    runtime = cfg.get("_runtime")
    if isinstance(runtime, dict) and runtime.get("dry_run") is True:
        return True
    return bool(gemini_cfg.get("dry_run", False))


def _resolve_api_key(gemini_cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    candidates: list[str] = []
    configured = gemini_cfg.get("api_key_env")
    if isinstance(configured, str) and configured:
        candidates.append(configured)
    candidates.extend(["GEMINI_API_KEY", "GOOGLE_API_KEY"])

    seen: set[str] = set()
    ordered = [name for name in candidates if not (name in seen or seen.add(name))]
    for env_name in ordered:
        value = os.environ.get(env_name)
        if value:
            return value, env_name
    return None, None


@dataclass
class ErrorInfo:
    status_code: int | None
    error_code: str
    message: str
    retryable: bool


class GeminiWorkloadDriver:
    def __init__(
        self,
        cfg: dict[str, Any],
        *,
        client: Any | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        gemini_cfg = _extract_gemini_cfg(cfg)

        self.cfg = cfg
        self.gemini_cfg = gemini_cfg
        self.model = str(gemini_cfg.get("model", "gemini-2.5-flash-lite"))
        self.timeout_seconds = int(gemini_cfg.get("timeout_seconds", 45))
        self.max_retries = int(gemini_cfg.get("max_retries", 5))
        self.backoff_initial_ms = int(gemini_cfg.get("backoff_initial_ms", 300))
        self.backoff_max_ms = int(gemini_cfg.get("backoff_max_ms", 5000))
        self.prompt_template = str(
            gemini_cfg.get(
                "prompt_template",
                "You are a deterministic benchmark assistant.\n"
                "Return concise output only.\n"
                "INPUT:\n"
                "{{input_text}}",
            )
        )
        self.dry_run = _runtime_dry_run(cfg, gemini_cfg)
        self.sleep_fn = sleep_fn
        self.rng = rng or random.Random(int(cfg.get("experiment", {}).get("order_seed", 20260213)))

        self.generation_cfg: dict[str, Any] = {
            "temperature": float(gemini_cfg.get("temperature", 0.0)),
            "max_output_tokens": int(gemini_cfg.get("max_output_tokens", 256)),
            "top_p": float(gemini_cfg.get("top_p", 1.0)),
            "top_k": int(gemini_cfg.get("top_k", 40)),
            "candidate_count": int(gemini_cfg.get("candidate_count", 1)),
        }

        self.api_key, self.api_key_env_used = _resolve_api_key(gemini_cfg)
        self._client = client
        self._init_error: ErrorInfo | None = None

    def run_baseline(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run_generate_phase("baseline", payload)

    def run_pcs_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run_generate_phase("pcs_run", payload)

    def run_pcs_verify(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run_local_verify(payload)

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        if self.dry_run:
            return
        if genai is None or genai_types is None:
            self._init_error = ErrorInfo(
                status_code=None,
                error_code="missing_dependency_google_genai",
                message="google-genai is not installed. Run: pip install google-genai",
                retryable=False,
            )
            return
        if not self.api_key:
            self._init_error = ErrorInfo(
                status_code=None,
                error_code="missing_api_key",
                message="Set GEMINI_API_KEY or GOOGLE_API_KEY in environment.",
                retryable=False,
            )
            return
        try:
            http_options = genai_types.HttpOptions(
                timeout=self.timeout_seconds,
                retry_options=genai_types.HttpRetryOptions(attempts=1),
            )
            self._client = genai.Client(api_key=self.api_key, http_options=http_options)
        except Exception as exc:  # noqa: BLE001
            self._init_error = ErrorInfo(
                status_code=None,
                error_code="client_init_error",
                message=str(exc),
                retryable=False,
            )

    def _render_prompt(self, input_text: str) -> str:
        if "{{input_text}}" in self.prompt_template:
            return self.prompt_template.replace("{{input_text}}", input_text)
        return f"{self.prompt_template}\n{input_text}"

    def _request_hash(self, phase: str, payload: dict[str, Any], prompt: str) -> str:
        filtered_payload = {
            "workload_id": payload.get("workload_id"),
            "complexity_tier": payload.get("complexity_tier"),
            "campaign_idx": payload.get("campaign_idx"),
            "episode_idx": payload.get("episode_idx"),
            "seed": payload.get("seed"),
            "concurrency": payload.get("concurrency"),
            "cell_id": payload.get("cell_id"),
        }
        req_obj = {
            "phase": phase,
            "model": self.model,
            "prompt": prompt,
            "generation_config": self.generation_cfg,
            "payload": filtered_payload,
        }
        return canonical_json_hash(req_obj)

    def _extract_usage(self, response: Any) -> dict[str, int | None]:
        usage_obj = {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return usage_obj
        usage_obj["input_tokens"] = _safe_int(getattr(usage, "prompt_token_count", None))
        usage_obj["output_tokens"] = _safe_int(getattr(usage, "candidates_token_count", None))
        usage_obj["total_tokens"] = _safe_int(getattr(usage, "total_token_count", None))
        return usage_obj

    def _extract_text(self, response: Any) -> str | None:
        text = None
        try:
            text = getattr(response, "text", None)
        except Exception:
            text = None
        if isinstance(text, str) and text:
            return text

        candidates = getattr(response, "candidates", None)
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            content = getattr(first, "content", None)
            parts = getattr(content, "parts", None)
            if isinstance(parts, list):
                out_parts: list[str] = []
                for part in parts:
                    p_text = getattr(part, "text", None)
                    if isinstance(p_text, str):
                        out_parts.append(p_text)
                joined = "".join(out_parts).strip()
                if joined:
                    return joined
        return None

    def _is_timeout_exception(self, exc: Exception) -> bool:
        if isinstance(exc, TimeoutError):
            return True
        if httpx is not None:
            timeout_types = (
                getattr(httpx, "TimeoutException", tuple()),
                getattr(httpx, "ReadTimeout", tuple()),
                getattr(httpx, "ConnectTimeout", tuple()),
                getattr(httpx, "WriteTimeout", tuple()),
                getattr(httpx, "PoolTimeout", tuple()),
            )
            timeout_flat = tuple(t for t in timeout_types if isinstance(t, type))
            if timeout_flat and isinstance(exc, timeout_flat):
                return True
        return False

    def _classify_exception(self, exc: Exception) -> ErrorInfo:
        if genai_errors is not None and isinstance(exc, genai_errors.APIError):
            status_code = _safe_int(getattr(exc, "code", None))
            message = str(getattr(exc, "message", str(exc)))
            if status_code == 429:
                return ErrorInfo(status_code, "api_429", message, True)
            if status_code is not None and status_code >= 500:
                return ErrorInfo(status_code, f"api_{status_code}", message, True)
            if status_code is not None and 400 <= status_code < 500:
                return ErrorInfo(status_code, f"api_{status_code}", message, False)
            return ErrorInfo(status_code, "api_error", message, True)

        if self._is_timeout_exception(exc):
            return ErrorInfo(None, "timeout", str(exc), True)

        if httpx is not None:
            transport_types = (
                getattr(httpx, "NetworkError", tuple()),
                getattr(httpx, "TransportError", tuple()),
            )
            transport_flat = tuple(t for t in transport_types if isinstance(t, type))
            if transport_flat and isinstance(exc, transport_flat):
                return ErrorInfo(None, "transport_error", str(exc), True)

        return ErrorInfo(None, exc.__class__.__name__.lower(), str(exc), False)

    def _backoff_seconds(self, retry_index: int) -> float:
        raw_ms = min(self.backoff_max_ms, int(self.backoff_initial_ms * (2 ** retry_index)))
        jitter = self.rng.uniform(0.8, 1.2)
        return max(0.0, (raw_ms * jitter) / 1000.0)

    def _fail_result(
        self,
        *,
        request_hash: str,
        latency_ms: float,
        status_code: int | None,
        error_code: str,
        error_message: str,
        started_utc: str,
        finished_utc: str,
        retry_count: int,
        attempt_count: int,
        phase: str,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "latency_ms": float(latency_ms),
            "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
            "status_code": status_code,
            "error_code": error_code,
            "error_message": error_message,
            "response_text": None,
            "request_hash": request_hash,
            "response_hash": None,
            "retry_count": int(retry_count),
            "attempt_count": int(attempt_count),
            "started_utc": started_utc,
            "finished_utc": finished_utc,
            "model": self.model,
            "phase": phase,
            "input_hash": None,
            "api_key_env_used": self.api_key_env_used,
        }

    def _run_generate_phase(self, phase: str, payload: dict[str, Any]) -> dict[str, Any]:
        input_text = str(payload.get("input_text", ""))
        prompt = self._render_prompt(input_text)
        request_hash = self._request_hash(phase, payload, prompt)
        input_hash = sha256_text(input_text)

        self._ensure_client()
        if self._init_error is not None:
            now = utc_now()
            return {
                **self._fail_result(
                    request_hash=request_hash,
                    latency_ms=0.0,
                    status_code=self._init_error.status_code,
                    error_code=self._init_error.error_code,
                    error_message=self._init_error.message,
                    started_utc=now,
                    finished_utc=now,
                    retry_count=0,
                    attempt_count=0,
                    phase=phase,
                ),
                "input_hash": input_hash,
            }

        if self.dry_run:
            now = utc_now()
            dry_text = f"dry_run:{phase}:{input_hash[:12]}"
            return {
                "ok": True,
                "latency_ms": 0.0,
                "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
                "status_code": 200,
                "error_code": None,
                "error_message": None,
                "response_text": dry_text,
                "request_hash": request_hash,
                "response_hash": sha256_text(dry_text),
                "retry_count": 0,
                "attempt_count": 1,
                "started_utc": now,
                "finished_utc": now,
                "model": self.model,
                "phase": phase,
                "input_hash": input_hash,
                "api_key_env_used": self.api_key_env_used,
            }

        last_error: ErrorInfo | None = None
        started_utc = utc_now()
        final_finished = started_utc
        total_latency_ms = 0.0
        attempts = max(0, self.max_retries) + 1
        retries_used = 0
        attempts_used = 0

        for attempt in range(attempts):
            attempts_used = attempt + 1
            started_utc = utc_now()
            t0 = time.perf_counter()
            try:
                assert self._client is not None
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(**self.generation_cfg),
                )
                latency_ms = (time.perf_counter() - t0) * 1000.0
                total_latency_ms += latency_ms
                finished_utc = utc_now()

                response_text = self._extract_text(response)
                response_hash = sha256_text(response_text) if isinstance(response_text, str) else None

                return {
                    "ok": True,
                    "latency_ms": float(total_latency_ms),
                    "usage": self._extract_usage(response),
                    "status_code": 200,
                    "error_code": None,
                    "error_message": None,
                    "response_text": response_text,
                    "request_hash": request_hash,
                    "response_hash": response_hash,
                    "retry_count": int(attempt),
                    "attempt_count": int(attempt + 1),
                    "started_utc": started_utc,
                    "finished_utc": finished_utc,
                    "model": self.model,
                    "phase": phase,
                    "input_hash": input_hash,
                    "api_key_env_used": self.api_key_env_used,
                }
            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.perf_counter() - t0) * 1000.0
                total_latency_ms += latency_ms
                final_finished = utc_now()
                info = self._classify_exception(exc)
                last_error = info
                should_retry = info.retryable and attempt < self.max_retries
                if should_retry:
                    retries_used += 1
                    self.sleep_fn(self._backoff_seconds(attempt))
                    continue
                break

        if last_error is None:
            last_error = ErrorInfo(None, "unknown_error", "unknown failure", False)

        return {
            **self._fail_result(
                request_hash=request_hash,
                latency_ms=total_latency_ms,
                status_code=last_error.status_code,
                error_code=last_error.error_code,
                error_message=last_error.message,
                started_utc=started_utc,
                finished_utc=final_finished,
                retry_count=retries_used,
                attempt_count=attempts_used,
                phase=phase,
            ),
            "input_hash": input_hash,
        }

    def _run_local_verify(self, payload: dict[str, Any]) -> dict[str, Any]:
        input_text = str(payload.get("input_text", ""))
        prompt = self._render_prompt(input_text)
        request_hash = self._request_hash("pcs_verify", payload, prompt)
        current_input_hash = sha256_text(input_text)
        run_input_hash = payload.get("run_input_hash")
        run_response_hash = payload.get("run_response_hash")
        expected_response_hash = payload.get("expected_response_hash", run_response_hash)

        started_utc = utc_now()
        t0 = time.perf_counter()

        if not isinstance(run_response_hash, str) or not run_response_hash:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return {
                **self._fail_result(
                    request_hash=request_hash,
                    latency_ms=latency_ms,
                    status_code=422,
                    error_code="missing_run_response_hash",
                    error_message="PCS verify requires run_response_hash.",
                    started_utc=started_utc,
                    finished_utc=utc_now(),
                    retry_count=0,
                    attempt_count=1,
                    phase="pcs_verify",
                ),
                "input_hash": current_input_hash,
            }

        if isinstance(run_input_hash, str) and run_input_hash and run_input_hash != current_input_hash:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return {
                **self._fail_result(
                    request_hash=request_hash,
                    latency_ms=latency_ms,
                    status_code=422,
                    error_code="invocation_mismatch",
                    error_message="input_hash mismatch between run and verify.",
                    started_utc=started_utc,
                    finished_utc=utc_now(),
                    retry_count=0,
                    attempt_count=1,
                    phase="pcs_verify",
                ),
                "response_hash": run_response_hash,
                "input_hash": current_input_hash,
            }

        if isinstance(expected_response_hash, str) and expected_response_hash and run_response_hash != expected_response_hash:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return {
                **self._fail_result(
                    request_hash=request_hash,
                    latency_ms=latency_ms,
                    status_code=422,
                    error_code="response_hash_mismatch",
                    error_message="run_response_hash does not match expected_response_hash.",
                    started_utc=started_utc,
                    finished_utc=utc_now(),
                    retry_count=0,
                    attempt_count=1,
                    phase="pcs_verify",
                ),
                "response_hash": run_response_hash,
                "input_hash": current_input_hash,
            }

        latency_ms = (time.perf_counter() - t0) * 1000.0
        finished_utc = utc_now()
        return {
            "ok": True,
            "latency_ms": float(latency_ms),
            "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
            "status_code": 200,
            "error_code": None,
            "error_message": None,
            "response_text": None,
            "request_hash": request_hash,
            "response_hash": run_response_hash,
            "retry_count": 0,
            "attempt_count": 1,
            "started_utc": started_utc,
            "finished_utc": finished_utc,
            "model": self.model,
            "phase": "pcs_verify",
            "input_hash": current_input_hash,
            "api_key_env_used": self.api_key_env_used,
        }


_DRIVER_CACHE: dict[str, GeminiWorkloadDriver] = {}


def _driver_cache_key(cfg: dict[str, Any]) -> str:
    gemini_cfg = _extract_gemini_cfg(cfg)
    key_obj = {
        "model": gemini_cfg.get("model", "gemini-2.5-flash-lite"),
        "timeout_seconds": gemini_cfg.get("timeout_seconds", 45),
        "max_retries": gemini_cfg.get("max_retries", 5),
        "backoff_initial_ms": gemini_cfg.get("backoff_initial_ms", 300),
        "backoff_max_ms": gemini_cfg.get("backoff_max_ms", 5000),
        "temperature": gemini_cfg.get("temperature", 0.0),
        "max_output_tokens": gemini_cfg.get("max_output_tokens", 256),
        "top_p": gemini_cfg.get("top_p", 1.0),
        "top_k": gemini_cfg.get("top_k", 40),
        "candidate_count": gemini_cfg.get("candidate_count", 1),
        "prompt_template": gemini_cfg.get("prompt_template", ""),
        "api_key_env": gemini_cfg.get("api_key_env", "GEMINI_API_KEY"),
        "dry_run": _runtime_dry_run(cfg, gemini_cfg),
    }
    return canonical_json_hash(key_obj)


def _get_driver(cfg: dict[str, Any]) -> GeminiWorkloadDriver:
    key = _driver_cache_key(cfg)
    cached = _DRIVER_CACHE.get(key)
    if cached is not None:
        return cached
    driver = GeminiWorkloadDriver(cfg)
    _DRIVER_CACHE[key] = driver
    return driver


def reset_driver_cache() -> None:
    _DRIVER_CACHE.clear()


def run_baseline(payload: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return _get_driver(cfg).run_baseline(payload)


def run_pcs_run(payload: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return _get_driver(cfg).run_pcs_run(payload)


def run_pcs_verify(payload: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return _get_driver(cfg).run_pcs_verify(payload)
