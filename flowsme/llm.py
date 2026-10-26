"""Client for any OpenAI-compatible chat endpoint: Meta Model API, Ollama (local Llama), Together, DeepInfra,
OpenRouter, vLLM... Two roles, each with its own model and (optionally) its own endpoint:
  vision - describes screenshots (many small calls: a cheap or local model is fine)
  text   - names tasks and writes recommendations (few calls: worth a stronger model)

Every call is metered (tokens, web searches, audio seconds, estimated dollars), stops at the spending cap
(`max_cost_usd`), and is cached: an identical request is never paid for twice. Calls are thread-safe so
they can run in parallel. Every call can fail; callers fall back to heuristics."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .config import LLMConfig

ROLES = ("vision", "text")
# USD per 1M tokens: (input, cached input, output). Meta Model API prices, Oct 2026.
PRICES = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.2": (1.25, 0.15, 4.25),  # not published separately: assumed equal to 1.3
    "muse-spark-1.1": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "muse-spark-1.2-contributor": (0.10, 0.002, 0.20),
    "muse-wonder-1.0": (4.00, 0.20, 20.00),
    "muse-voice-transcribe-1.0": (0.0, 0.0, 0.0),  # billed per hour of audio (TRANSCRIBE_USD_PER_HOUR), not tokens
}
WEB_SEARCH_USD = 2.50 / 1000
TRANSCRIBE_USD_PER_HOUR = 0.18


class LLM:
    def __init__(self, cfg: LLMConfig):
        if cfg.provider not in ("offline", "openai_compatible"):
            raise SystemExit(f"Unknown llm.provider '{cfg.provider}' (use 'offline' or 'openai_compatible')")
        self.cfg = cfg
        self.enabled = cfg.provider == "openai_compatible"
        self.unreachable = False
        self.over_budget = False
        self.used_roles: set[str] = set()     # roles whose model answered at least once
        self.failed_roles: set[str] = set()   # roles switched off after repeated failures
        self.usage = {"calls": 0, "cache_hits": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0,
                      "searches": 0, "audio_seconds": 0.0, "cost_usd": 0.0, "unpriced_models": []}
        self._failures: dict[str, int] = {}
        self._unsupported: dict[str, set[str]] = {}  # endpoint -> optional features it rejected
        self._clients: dict[tuple[str, str], object] = {}
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._cache_path: Path | None = None
        self._endpoints = {
            "text": (cfg.base_url, cfg.api_key_env),
            "vision": (cfg.vision_base_url or cfg.base_url, cfg.vision_api_key_env or cfg.api_key_env),
        }
        contributor = [m for m in (cfg.vision_model, cfg.text_model) if m.endswith("-contributor")]
        if self.enabled and contributor and not cfg.allow_contributor:
            raise SystemExit(f"{', '.join(contributor)} is a contributor model: Meta may use what you send (screenshots "
                             "included) to train its models, and its terms forbid confidential or personal data. Use "
                             "the standard model, or set allow_contributor = true for test data only.")

    # ---- metering, budget and cache -----------------------------------------------------------------

    def use_cache(self, path: Path) -> None:
        """Remember replies in `path`: re-running an analysis doesn't pay for the same request again."""
        self._cache_path = path
        if path.exists():
            try:
                self._cache = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._cache = {}

    def _cached(self, key: str):
        with self._lock:
            if key in self._cache:
                self.usage["cache_hits"] += 1
                return True, self._cache[key]
        return False, None

    def _remember(self, key: str, value) -> None:
        if self._cache_path is None or value is None:
            return
        with self._lock:
            self._cache[key] = value
            self._cache_path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _key(**payload) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _charge(self, model: str, url: str, inp: int = 0, cached: int = 0, out: int = 0, searches: int = 0,
                audio_seconds: float = 0.0) -> None:
        local = any(h in url for h in ("localhost", "127.0.0.1", "[::1]"))
        price = (0.0, 0.0, 0.0) if local else PRICES.get(model)
        with self._lock:
            u = self.usage
            u["calls"] += 1
            u["input_tokens"] += inp
            u["cached_tokens"] += cached
            u["output_tokens"] += out
            u["searches"] += searches
            u["audio_seconds"] += audio_seconds
            if price is None:
                if model not in u["unpriced_models"]:
                    u["unpriced_models"].append(model)
                price = (0.0, 0.0, 0.0)
            u["cost_usd"] += ((max(inp - cached, 0) * price[0] + cached * price[1] + out * price[2]) / 1e6
                              + (0 if local else searches * WEB_SEARCH_USD
                                 + audio_seconds / 3600 * TRANSCRIBE_USD_PER_HOUR))
            if self.cfg.max_cost_usd and u["cost_usd"] >= self.cfg.max_cost_usd and not self.over_budget:
                self.over_budget = True
                print(f"  Spending cap of ${self.cfg.max_cost_usd:.2f} reached: built-in rules take over for the rest "
                      "of this run.")

    def cost_summary(self) -> str:
        u = self.usage
        text = (f"{u['calls']} AI calls ({u['cache_hits']} answered from cache), {u['input_tokens']:,} input / "
                f"{u['output_tokens']:,} output tokens")
        if u["searches"]:
            text += f", {u['searches']} web searches"
        if u["audio_seconds"]:
            text += f", {u['audio_seconds'] / 60:.1f} min of audio"
        text += f": about ${u['cost_usd']:.2f}"
        if u["unpriced_models"]:
            text += f" (not priced: {', '.join(u['unpriced_models'])})"
        return text

    # ---- calls ----------------------------------------------------------------------------------------

    def model(self, role: str) -> str:
        return self.cfg.vision_model if role == "vision" else self.cfg.text_model

    def available(self, role: str = "text") -> bool:
        return self.enabled and role not in self.failed_roles and not self.over_budget

    def chat(self, prompt: str, *, role: str = "text", system: str | None = None,
             images: tuple[str, ...] | list[str] = (), max_tokens: int = 800, json_mode: bool = False,
             schema: dict | None = None, effort: str | None = None, _plain: bool = False) -> str | None:
        """Send one prompt (plus optional base64 JPEG images); return the reply text or None."""
        if not self.available(role):
            return None
        import openai

        model = self.model(role)
        effort = effort or (self.cfg.vision_reasoning_effort if role == "vision" else "") or self.cfg.reasoning_effort
        key = self._key(kind="chat", model=model, system=system, prompt=prompt, schema=schema, effort=effort,
                        json_mode=json_mode, images=[hashlib.sha256(b.encode()).hexdigest() for b in images])
        hit, value = self._cached(key)
        if hit:
            return value
        url, client = self._client(role)
        messages = [{"role": "system", "content": system}] if system else []
        if images:
            content = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}} for b64 in images]
            content.append({"type": "text", "text": prompt})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        # Optional features; an endpoint that rejects them gets the plain request from then on.
        unsupported = self._unsupported.setdefault(url, set())
        extras: dict = {}
        used: list[str] = []
        if not _plain and effort and "reasoning_effort" not in unsupported:
            extras["extra_body"] = {"reasoning_effort": effort}
            used.append("reasoning_effort")
        max_tokens = max(max_tokens, self.cfg.min_output_tokens)
        if not _plain and schema and "json_schema" not in unsupported:
            # Structured output: the reply must match the schema (no broken or partial JSON).
            extras["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "result", "schema": schema, "strict": True}}
            used.append("json_schema")
        elif not _plain and (json_mode or schema) and "json_mode" not in unsupported:
            extras["response_format"] = {"type": "json_object"}
            used.append("json_mode")
        try:
            resp = client.chat.completions.create(model=model, messages=messages, temperature=self.cfg.temperature,
                                                  max_tokens=max_tokens, **extras)
        except openai.BadRequestError as exc:
            if extras:
                text = self.chat(prompt, role=role, system=system, images=images, max_tokens=max_tokens,
                                 effort=effort, _plain=True)
                if text is not None:  # the plain request worked, so the extras were the problem
                    with self._lock:
                        unsupported.update(used)
                return text
            self._fail(role, f"call failed: {exc}")
            return None
        except (openai.APIConnectionError, openai.AuthenticationError) as exc:
            with self._lock:
                if role not in self.failed_roles:
                    self.failed_roles.add(role)
                    self.unreachable = True
                    print(f"  {model}: cannot use {url} ({exc}). Heuristics fill in for it.")
            return None
        except openai.APIError as exc:
            self._fail(role, f"call failed: {exc}")
            return None
        usage = resp.usage
        if usage:
            details = getattr(usage, "prompt_tokens_details", None)
            self._charge(model, url, usage.prompt_tokens or 0, getattr(details, "cached_tokens", 0) or 0,
                         usage.completion_tokens or 0)
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        if not text and choice.finish_reason == "length":
            # Thinking models can spend the whole token budget reasoning before they answer.
            self._fail(role, "ran out of tokens before answering (probably a 'thinking' model); lower the "
                             "reasoning effort or raise min_output_tokens under [llm] in the config")
            return None
        with self._lock:
            self._failures[role] = 0
            self.used_roles.add(role)
        self._remember(key, text or None)
        return text or None

    def grounded(self, question: str, schema: dict | None = None) -> dict | None:
        """Answer with the model's own web search (Responses API `web_search` tool, e.g. Meta Model API).
        Returns {"answer", "data" (parsed when a schema is given), "citations", "results"}, or None when
        grounding is off, the endpoint has no web search, or the call fails (callers then use DuckDuckGo)."""
        if not (self.cfg.grounded_search and self.available("text")):
            return None
        import openai

        url, client = self._client("text")
        unsupported = self._unsupported.setdefault(url, set())
        if "web_search" in unsupported:
            return None
        model = self.model("text")
        key = self._key(kind="grounded", model=model, question=question, schema=schema,
                        size=self.cfg.search_context_size)
        hit, value = self._cached(key)
        if hit:
            return value
        request = {"model": model, "input": question, "store": False,
                   "tools": [{"type": "web_search", "search_context_size": self.cfg.search_context_size}],
                   "include": ["web_search_call.results"],
                   "max_output_tokens": max(2000, self.cfg.min_output_tokens),
                   "reasoning": {"effort": self.cfg.search_reasoning_effort or self.cfg.reasoning_effort or "low"}}
        if schema:
            request["text"] = {"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}}
        try:
            data = client.responses.create(**request).model_dump()
        except openai.APIError as exc:
            # Only web search is switched off: the model itself may be fine for everything else.
            with self._lock:
                if "web_search" not in unsupported:
                    unsupported.add("web_search")
                    print(f"  {model}: web search unavailable on {url} "
                          f"({getattr(exc, 'status_code', None) or type(exc).__name__}); using DuckDuckGo instead.")
            return None
        answer, citations, results, searches = "", {}, [], 0
        for item in data.get("output") or []:
            if item.get("type") == "web_search_call":
                searches += 1
                results += [{"title": r.get("title") or "", "url": r.get("url") or "",
                             "snippet": (r.get("snippet") or "")[:300]} for r in item.get("results") or []]
            elif item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        answer += part.get("text") or ""
                        for note in part.get("annotations") or []:
                            if note.get("type") == "url_citation" and note.get("url"):
                                citations[note["url"]] = {"title": note.get("title") or "", "url": note["url"]}
        usage = data.get("usage") or {}
        self._charge(model, url, usage.get("input_tokens") or 0,
                     (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0,
                     usage.get("output_tokens") or 0, searches=searches)
        with self._lock:
            self.used_roles.add("text")
        result = {"answer": answer.strip(), "data": parse_json(answer) if schema else None,
                  "citations": list(citations.values()), "results": results}
        self._remember(key, result)
        return result

    def transcribe(self, wav: bytes, seconds: float, mode: str = "ENDPOINTING") -> dict | None:
        """Speech to text with Meta's Muse Voice Transcribe (POST /asr/transcribe, multipart WAV, up to 10
        minutes per request). Returns {"transcript", "turns": [{"startMs", "endMs", "transcript"}]} or None."""
        if not self.available("text"):
            return None
        key = self._key(kind="asr", model=self.cfg.transcribe_model, audio=hashlib.sha256(wav).hexdigest(), mode=mode)
        hit, value = self._cached(key)
        if hit:
            return value
        url, key_env = self._endpoints["text"]
        boundary = uuid.uuid4().hex
        request = json.dumps({"model": self.cfg.transcribe_model, "audioEncoding": "WAV", "mode": mode})
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="request"\r\n'
                f"Content-Type: application/json\r\n\r\n{request}\r\n"
                f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="narration.wav"\r\n'
                f"Content-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{url.rstrip('/')}/asr/transcribe", data=body, method="POST", headers={
            "Authorization": f"Bearer {os.environ.get(key_env, '')}", "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            print(f"  {self.cfg.transcribe_model}: transcription failed ({exc.code}): "
                  f"{exc.read().decode(errors='replace')[:200]}")
            return None
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  {self.cfg.transcribe_model}: cannot reach {url} ({exc}).")
            return None
        self._charge(self.cfg.transcribe_model, url, audio_seconds=seconds)
        self._remember(key, data)
        return data

    def chat_json(self, prompt: str, schema: dict | None = None, **kwargs):
        """A JSON reply: schema-exact where the endpoint supports structured output, else JSON mode."""
        prompt += "\n\nReply with valid JSON only, no other text."
        for _ in range(2):  # small models now and then emit broken JSON; one retry fixes most of it
            data = parse_json(self.chat(prompt, json_mode=True, schema=schema, **kwargs))
            if data is not None or not self.available(kwargs.get("role", "text")):
                return data
        return None

    def _client(self, role: str):
        url, key_env = self._endpoints[role]
        with self._lock:
            client = self._clients.get((url, key_env))
            if client is None:
                from openai import OpenAI

                client = OpenAI(base_url=url, api_key=os.environ.get(key_env) or "not-needed",
                                timeout=self.cfg.timeout, max_retries=1)
                self._clients[(url, key_env)] = client
        return url, client

    def _fail(self, role: str, why: str) -> None:
        """Three failures in a row switch off that role only; the other keeps working."""
        model = self.model(role)
        with self._lock:
            print(f"  {model} {why}")
            self._failures[role] = self._failures.get(role, 0) + 1
            if self._failures[role] >= 3 and role not in self.failed_roles:
                self.failed_roles.add(role)
                print(f"  {model} switched off for the rest of this run; heuristics fill in for it.")


def parse_json(text: str | None):
    """Pull the first JSON object/array out of a model reply (tolerates code fences and chatter)."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None
