"""Client for the codec-agent Hermes Agent API server (OpenAI-compatible).

Adapted from hls-rtl api/hermes_engine.py, stdlib-only so the pipeline keeps no
third-party runtime dependency.

Server: hermes profile `codecagent`, api_server platform on 127.0.0.1:8659, model
name `codec-agent`, tools disabled for that platform (generation is one turn).
Settings come from the environment, falling back to the git-ignored `.env` at the
repo root:
  HERMES_BASE_URL   default http://127.0.0.1:8659
  HERMES_API_KEY    the profile's API_SERVER_KEY (never commit it)
  HERMES_MODEL      default codec-agent
  HERMES_TIMEOUT    seconds, default 600
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
_TRANSIENT = (429, 500, 502, 503, 504)


def _dotenv() -> Dict[str, str]:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    timeout: float

    @classmethod
    def load(cls) -> "Settings":
        file_env = _dotenv()
        get = lambda k, d="": os.environ.get(k) or file_env.get(k) or d  # noqa: E731
        return cls(base_url=get("HERMES_BASE_URL", "http://127.0.0.1:8659").rstrip("/"),
                   api_key=get("HERMES_API_KEY"),
                   model=get("HERMES_MODEL", "codec-agent"),
                   timeout=float(get("HERMES_TIMEOUT", "600")))


def upstream_model(profile: str = "codecagent") -> Optional[str]:
    """The LLM the hermes profile calls (config.yaml `model.default`) — not a secret."""
    import re
    cfg = Path.home() / ".hermes" / "profiles" / profile / "config.yaml"
    if not cfg.exists():
        return None
    m = re.search(r"^model:\s*\n(?:[ \t]+.*\n)*?[ \t]+default:\s*(\S+)", cfg.read_text(), re.M)
    return m.group(1) if m else None


class HermesError(RuntimeError):
    pass


@dataclass
class HermesReply:
    content: str
    usage: Dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


class HermesClient:
    def __init__(self, settings: Optional[Settings] = None, *, name: str = "codec-agent"):
        self.s = settings or Settings.load()
        self.name = name

    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 headers: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.s.base_url}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.s.api_key:
            req.add_header("Authorization", f"Bearer {self.s.api_key}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout or self.s.timeout) as resp:
            return json.loads(resp.read().decode())

    def health(self) -> dict:
        """Liveness plus an authenticated model listing — "ok" alone does not prove the key works."""
        out = {"base_url": self.s.base_url, "model": self.s.model, "key_configured": bool(self.s.api_key)}
        try:
            out["health"] = self._request("GET", "/health", timeout=5)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"unreachable: {exc}"
            return out
        try:
            models = [m["id"] for m in self._request("GET", "/v1/models", timeout=10).get("data", [])]
            out["models"] = models
            out["model_available"] = self.s.model in models
        except urllib.error.HTTPError as exc:
            out["error"] = f"/v1/models HTTP {exc.code} (check HERMES_API_KEY)"
        return out

    def chat(self, prompt: str, *, system: str = "", session_id: Optional[str] = None,
             retries: int = 3) -> HermesReply:
        """One stateless turn by default: a fresh session id per call, so a repair prompt
        never inherits a stale transcript — everything needed is in the prompt."""
        if not self.s.api_key:
            raise HermesError("HERMES_API_KEY is not set (see .env / README)")
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
        body = {"model": self.s.model, "messages": messages, "stream": False}
        headers = {"X-Hermes-Session-Key": f"codec-agent:{self.name}",
                   "X-Hermes-Session-Id": f"{self.name}-{session_id or uuid.uuid4().hex}"}
        last: Optional[Exception] = None
        for attempt in range(retries):
            t0 = time.time()
            try:
                data = self._request("POST", "/v1/chat/completions", body, headers)
            except urllib.error.HTTPError as exc:
                last = HermesError(f"HTTP {exc.code}: {exc.read()[:300].decode(errors='replace')}")
                if exc.code in _TRANSIENT and attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise last from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise HermesError(f"Hermes API server unreachable at {self.s.base_url}: {exc}") from exc
            choice = (data.get("choices") or [{}])[0]
            content = ((choice.get("message") or {}).get("content") or "").strip()
            meta = data.get("hermes") or {}
            # The server reports upstream model failures as a 200 with finish_reason=error.
            if choice.get("finish_reason") == "error" or meta.get("failed"):
                last = HermesError(f"agent error: {meta.get('error') or content[:300]}")
                if "429" in str(meta.get("error", "")) and attempt < retries - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise last
            if not content:
                raise HermesError(f"empty reply: {json.dumps(data)[:300]}")
            return HermesReply(content, data.get("usage") or {}, time.time() - t0, data)
        raise last or HermesError("Hermes call failed after retries")


if __name__ == "__main__":
    print(json.dumps(HermesClient().health(), indent=2))
