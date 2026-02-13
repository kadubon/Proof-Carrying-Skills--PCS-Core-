from __future__ import annotations

import random
from pathlib import Path

import pytest
from google.genai.errors import APIError

import PoC2.gemini_workload_driver as gemini_workload_driver
from PoC2.gemini_workload_driver import GeminiWorkloadDriver


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int, total_tokens: int) -> None:
        self.prompt_token_count = input_tokens
        self.candidates_token_count = output_tokens
        self.total_token_count = total_tokens


class _FakeResponse:
    def __init__(self, text: str, usage: _FakeUsage | None = None) -> None:
        self.text = text
        self.usage_metadata = usage


class _FakeModels:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    def generate_content(self, **_: object) -> object:
        out = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(out, Exception):
            raise out
        return out


class _FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.models = _FakeModels(outcomes)


def _cfg(max_retries: int = 3) -> dict[str, object]:
    return {
        "experiment": {"order_seed": 20260213},
        "workload": {
            "gemini": {
                "model": "gemini-2.5-flash-lite",
                "timeout_seconds": 10,
                "max_retries": max_retries,
                "backoff_initial_ms": 1,
                "backoff_max_ms": 2,
                "temperature": 0.0,
                "max_output_tokens": 32,
                "top_p": 1.0,
                "top_k": 1,
                "candidate_count": 1,
                "prompt_template": "INPUT:\n{{input_text}}",
            }
        },
    }


def test_retry_then_success() -> None:
    outcomes = [
        APIError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "rate limited"}}),
        _FakeResponse("ok", _FakeUsage(11, 7, 18)),
    ]
    driver = GeminiWorkloadDriver(_cfg(max_retries=2), client=_FakeClient(outcomes), sleep_fn=lambda _: None, rng=random.Random(1))
    result = driver.run_baseline({"input_text": "hello", "workload_id": "w1", "campaign_idx": 0, "episode_idx": 0, "seed": 1, "concurrency": 1, "cell_id": "c1"})

    assert result["ok"] is True
    assert result["retry_count"] == 1
    assert result["status_code"] == 200
    assert result["usage"]["input_tokens"] == 11
    assert result["request_hash"]
    assert result["response_hash"]
    assert result["started_utc"]
    assert result["finished_utc"]


def test_timeout_fail_closed() -> None:
    outcomes = [TimeoutError("deadline exceeded"), TimeoutError("deadline exceeded")]
    driver = GeminiWorkloadDriver(_cfg(max_retries=1), client=_FakeClient(outcomes), sleep_fn=lambda _: None, rng=random.Random(2))
    result = driver.run_pcs_run({"input_text": "hello", "workload_id": "w1", "campaign_idx": 0, "episode_idx": 0, "seed": 1, "concurrency": 1, "cell_id": "c1"})

    assert result["ok"] is False
    assert result["error_code"] == "timeout"
    assert result["response_text"] is None
    assert result["retry_count"] == 1


def test_verify_invocation_mismatch_rejects() -> None:
    driver = GeminiWorkloadDriver(_cfg(), client=_FakeClient([]), sleep_fn=lambda _: None, rng=random.Random(3))
    result = driver.run_pcs_verify(
        {
            "input_text": "input-a",
            "run_input_hash": "deadbeef",
            "run_response_hash": "abc123",
            "expected_response_hash": "abc123",
            "workload_id": "w1",
            "campaign_idx": 0,
            "episode_idx": 0,
            "seed": 1,
            "concurrency": 1,
            "cell_id": "c1",
        }
    )

    assert result["ok"] is False
    assert result["status_code"] == 422
    assert result["error_code"] == "invocation_mismatch"


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_non_429_4xx_no_retry(status_code: int) -> None:
    outcomes = [APIError(status_code, {"error": {"status": "INVALID_ARGUMENT", "message": "bad req"}})]
    fake = _FakeClient(outcomes)
    driver = GeminiWorkloadDriver(_cfg(max_retries=5), client=fake, sleep_fn=lambda _: None, rng=random.Random(4))
    result = driver.run_baseline({"input_text": "hello", "workload_id": "w1", "campaign_idx": 0, "episode_idx": 0, "seed": 1, "concurrency": 1, "cell_id": "c1"})

    assert result["ok"] is False
    assert result["status_code"] == status_code
    assert result["retry_count"] == 0
    assert fake.models.calls == 1


def test_api_key_loaded_from_poc2_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    (tmp_path / "PoC2").mkdir(parents=True, exist_ok=True)
    (tmp_path / "PoC2/.env").write_text("GEMINI_API_KEY=file_key\n", encoding="utf-8")

    gemini_workload_driver.reset_driver_cache()
    driver = GeminiWorkloadDriver(_cfg(), client=_FakeClient([]), sleep_fn=lambda _: None, rng=random.Random(5))
    assert driver.api_key == "file_key"
    assert driver.api_key_env_used == "GEMINI_API_KEY@PoC2/.env"
