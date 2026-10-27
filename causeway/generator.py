"""Meterless Nightshift provider: the one injected generator every agent and engine uses.

Implements the Meterless Runtime provider contract v4 (meterless.ai/engines.html, POWER >
"Add Meterless as a provider"): URL normalisation, real bearer auth, the meterless-app /
meterless-scope headers, /v1/models as the online check, auto-detect, 180 s timeout.

On top of the contract: agents pin a concrete Nemotron model per role (verified to route
exactly there), Ultra's leaked reasoning tail is stripped, 429/5xx are retried with backoff,
and every call is written to the run's model_calls.jsonl with its compute receipt, which is
the per-call evidence of which model handled the request.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APP_ID = "causeway"
TIMEOUT_S = 180            # contract default for model discovery / short calls
CHAT_TIMEOUT_S = TIMEOUT_S # don't give up early: Nightshift keeps counting a call we abandoned as in flight, and
                           # enough of them lock the app out with cloud_busy until Nightshift restarts (seen twice)
FIRST_TOKEN_MS = 140_000   # sent as meterless-ttfb-ms; Nightshift's default cloud first-token limit is shorter
WAIT_S = 300               # how long one call waits out a transient Nightshift/NVIDIA state before giving up
# Transient states worth waiting for rather than spending model attempts on (all seen in real runs):
TRANSIENT = ("cloud_unreachable",   # the Mac lost its route to NVIDIA (DNS failures)
             "cloud_busy",          # too many calls in flight for this app, incl. ones we already abandoned
             "engine_not_running")  # Nightshift's engine restarting
DETECT_PORTS = (8088, 8090, 8787, 11434)
DETECT_HOSTS = ("127.0.0.1", "localhost", "[::1]")

# Role -> model. Overridable from .env so a flaky model can be swapped without code changes.
MODELS = {
    "fast": os.environ.get("CAUSEWAY_MODEL_FAST", "nvidia/nemotron-3.5-lightning-30b-a3b"),
    "deep": os.environ.get("CAUSEWAY_MODEL_DEEP", "nvidia/nemotron-3-ultra-550b-a55b"),
}


def normalise_base_url(raw: str) -> str:
    """Accept host, host:port, …/v1, …/v1/chat/completions, …/v1/models or …/healthz; return <host>/v1."""
    raw = raw.strip()
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    return f"{parsed.scheme}://{parsed.netloc}/v1"


@dataclass
class Completion:
    text: str
    model: str
    receipt_id: str | None
    latency_ms: int
    tokens_in: int | None
    tokens_out: int | None


class GeneratorError(RuntimeError):
    pass


class NightshiftGenerator:
    def __init__(self, base_url: str | None = None, token: str | None = None, evidence=None) -> None:
        self.base_url = normalise_base_url(base_url or os.environ.get("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1"))
        self.token = token or os.environ.get("METERLESS_RUNTIME_TOKEN", "")
        if not self.token or self.token == "rt-local":
            raise GeneratorError("METERLESS_RUNTIME_TOKEN is missing (copy it from Nightshift > Console)")
        self.evidence = evidence
        self._client = httpx.Client(timeout=TIMEOUT_S)

    def _headers(self, chat: bool = False) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "meterless-app": APP_ID}
        if chat:
            headers["meterless-scope"] = "user"
            headers["meterless-ttfb-ms"] = str(FIRST_TOKEN_MS)
        return headers

    def status(self) -> str:
        """'online', 'unauthorized' or 'offline', decided by GET /v1/models per the contract."""
        try:
            resp = self._client.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
        except httpx.HTTPError:
            return "offline"
        if resp.status_code in (401, 403):
            return "unauthorized"
        if resp.status_code == 200 and any(m.get("id", "").startswith("meterless/") for m in resp.json().get("data", [])):
            return "online"
        return "offline"

    def models(self) -> list[str]:
        resp = self._client.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]

    def __call__(self, prompt: str, system: str | None = None, role: str = "fast", agent: str = "", max_tokens: int | None = None) -> Completion:
        """The injected generator: (prompt, system) -> text, through Nightshift, logged.

        If the role's model hangs or keeps failing, fall back to the other Nemotron model (logged as a fallback)
        so one flaky endpoint can't kill an investigation; if that hangs too, the original model gets one more try.
        """
        try:
            return self._call(prompt, system, MODELS.get(role, role), agent, max_tokens, max_timeouts=1)
        except GeneratorError as exc:
            fallback = {"deep": "fast", "fast": "deep"}.get(role)
            if not fallback:
                raise
            if self.evidence:
                self.evidence.log("model_calls", {"agent": agent, "event": "fallback", "from": MODELS[role],
                                                  "to": MODELS[fallback], "reason": str(exc)[:300]})
            try:
                return self._call(prompt, system, MODELS[fallback], agent, max_tokens, max_timeouts=1)
            except GeneratorError:  # both hung once: give the original model one more, patient, try
                return self._call(prompt, system, MODELS[role], agent, max_tokens, max_timeouts=1)

    def _call(self, prompt: str, system: str | None, model: str, agent: str, max_tokens: int | None,
              max_timeouts: int = 2) -> Completion:
        # Nightshift's exact-match cache keys on the user message only (verified: a changed system prompt still
        # got a cached reply). Tag every request with the run id and a fingerprint of the system prompt so each
        # run is computed fresh and a stale answer can't be replayed as live agent work.
        tag = f"[run {self.evidence.run_id if self.evidence else 'adhoc'} · ctx {hashlib.sha1((system or '').encode()).hexdigest()[:8]}]"
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": f"{tag}\n{prompt}"}]
        body = {"model": model, "messages": messages}
        if max_tokens:
            body["max_tokens"] = max_tokens

        delay, last_error, timeouts, attempt, waited = 2.0, None, 0, 0, 0.0
        while attempt < 4:
            started = time.monotonic()
            transient = False
            try:
                resp = self._client.post(f"{self.base_url}/chat/completions", headers=self._headers(chat=True), json=body,
                                         timeout=CHAT_TIMEOUT_S)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                transient = isinstance(exc, (httpx.ConnectError, httpx.NetworkError))
                if isinstance(exc, httpx.TimeoutException):
                    timeouts += 1
                    if timeouts >= max_timeouts:  # a hung model rarely answers on retry: switch models instead
                        self._log_failure(model, agent, attempt, last_error)
                        break
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" not in data:
                        return self._completion(data, model, agent, int((time.monotonic() - started) * 1000))
                    last_error = str(data["error"])
                elif resp.status_code in (401, 403):
                    raise GeneratorError("Nightshift rejected the runtime token (unauthorized)")
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                    transient = any(code in resp.text for code in TRANSIENT)
                    if not transient and resp.status_code not in (408, 429) and resp.status_code < 500:
                        break
            self._log_failure(model, agent, attempt, last_error)
            if transient and waited < WAIT_S:
                # Wait it out without spending model attempts (or falling back, which would add another call
                # in flight), so a minute-long blip doesn't end a 20-minute investigation.
                pause = min(20.0, WAIT_S - waited)
                time.sleep(pause)
                waited += pause
                continue
            attempt += 1
            time.sleep(delay)
            delay *= 2
        if last_error and "cloud_busy" in last_error:
            raise GeneratorError(f"Nightshift still reports too many calls in flight after {waited:.0f} s: its counter "
                                 "may be stuck on abandoned calls. Quit and reopen Nightshift, then retry.")
        raise GeneratorError(f"{model} failed after retries: {last_error}")

    def _completion(self, data: dict, requested: str, agent: str, latency_ms: int) -> Completion:
        meta = data.get("meterless", {})
        receipt = meta.get("receipt", {}) if isinstance(meta.get("receipt"), dict) else {}
        usage = data.get("usage") or meta.get("usage") or {}
        text = strip_reasoning(data["choices"][0]["message"].get("content") or "")
        completion = Completion(
            text=text,
            model=meta.get("routed_model") or data.get("model") or requested,
            receipt_id=meta.get("receipt_id") or receipt.get("receipt_id"),
            latency_ms=latency_ms,
            tokens_in=usage.get("prompt_tokens", usage.get("input_tokens")),
            tokens_out=usage.get("completion_tokens", usage.get("output_tokens")),
        )
        if self.evidence:
            self.evidence.log("model_calls", {
                "agent": agent,
                "requested_model": requested,
                "served_model": completion.model,
                "provider": (meta.get("winning_route") or {}).get("provider"),
                "receipt_id": completion.receipt_id,
                "latency_ms": latency_ms,
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
                "from_cache": meta.get("from_cache"),
                "cache_status": (meta.get("cache") or {}).get("status"),
            })
        return completion

    def _log_failure(self, model: str, agent: str, attempt: int, error: str | None) -> None:
        if self.evidence:
            self.evidence.log("model_calls", {"agent": agent, "requested_model": model, "attempt": attempt, "error": error})


_THINK_TAIL = re.compile(r"^.*</think>", re.DOTALL)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Drop <think>…</think> blocks and any reasoning tail that ends in a bare </think>."""
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:
        text = _THINK_TAIL.sub("", text)
    return text.strip()


def auto_detect(token: str) -> str | None:
    """Return the first base URL that answers online or unauthorized, per the contract."""
    for host in DETECT_HOSTS:
        for port in DETECT_PORTS:
            url = f"http://{host}:{port}/v1"
            try:
                gen = NightshiftGenerator(url, token)
            except GeneratorError:
                return None
            if gen.status() in ("online", "unauthorized"):
                return url
    return None
