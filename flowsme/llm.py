"""Client for any OpenAI-compatible chat endpoint: Ollama (local Llama), Together, DeepInfra, OpenRouter,
vLLM... Two roles, each with its own model and (optionally) its own endpoint:
  vision - describes screenshots (many small calls: a cheap or local model is fine)
  text   - names tasks and writes recommendations (few calls: worth a stronger model)
Calls are thread-safe so they can run in parallel. Every call can fail; callers fall back to heuristics."""
from __future__ import annotations

import json
import os
import re
import threading

from .config import LLMConfig

ROLES = ("vision", "text")


class LLM:
    def __init__(self, cfg: LLMConfig):
        if cfg.provider not in ("offline", "openai_compatible"):
            raise SystemExit(f"Unknown llm.provider '{cfg.provider}' (use 'offline' or 'openai_compatible')")
        self.cfg = cfg
        self.enabled = cfg.provider == "openai_compatible"
        self.unreachable = False
        self.used_roles: set[str] = set()     # roles whose model answered at least once
        self.failed_roles: set[str] = set()   # roles switched off after repeated failures
        self._failures: dict[str, int] = {}
        self._unsupported: dict[str, set[str]] = {}  # endpoint -> optional features it rejected
        self._clients: dict[tuple[str, str], object] = {}
        self._lock = threading.Lock()
        self._endpoints = {
            "text": (cfg.base_url, cfg.api_key_env),
            "vision": (cfg.vision_base_url or cfg.base_url, cfg.vision_api_key_env or cfg.api_key_env),
        }
        contributor = [m for m in (cfg.vision_model, cfg.text_model) if m.endswith("-contributor")]
        if self.enabled and contributor and not cfg.allow_contributor:
            raise SystemExit(f"{', '.join(contributor)} is a contributor model: Meta may use what you send (screenshots "
                             "included) to train its models, and its terms forbid confidential or personal data. Use "
                             "the standard model, or set allow_contributor = true for test data only.")

    def model(self, role: str) -> str:
        return self.cfg.vision_model if role == "vision" else self.cfg.text_model

    def available(self, role: str = "text") -> bool:
        return self.enabled and role not in self.failed_roles

    def chat(self, prompt: str, *, role: str = "text", system: str | None = None,
             images: tuple[str, ...] | list[str] = (), max_tokens: int = 800, json_mode: bool = False,
             schema: dict | None = None, _plain: bool = False) -> str | None:
        """Send one prompt (plus optional base64 JPEG images); return the reply text or None."""
        if not self.available(role):
            return None
        import openai

        model = self.model(role)
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
        effort = (self.cfg.vision_reasoning_effort if role == "vision" else "") or self.cfg.reasoning_effort
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
                text = self.chat(prompt, role=role, system=system, images=images, max_tokens=max_tokens, _plain=True)
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
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        if not text and choice.finish_reason == "length":
            # Thinking models can spend the whole token budget reasoning before they answer.
            self._fail(role, "ran out of tokens before answering (probably a 'thinking' model); "
                             "set reasoning_effort = \"none\" under [llm] in config.toml")
            return None
        with self._lock:
            self._failures[role] = 0
            self.used_roles.add(role)
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
        request = {"model": self.model("text"), "input": question, "store": False,
                   "tools": [{"type": "web_search", "search_context_size": "medium"}],
                   "include": ["web_search_call.results"],
                   "max_output_tokens": max(2000, self.cfg.min_output_tokens)}
        if self.cfg.reasoning_effort:
            request["reasoning"] = {"effort": self.cfg.reasoning_effort}
        if schema:
            request["text"] = {"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}}
        try:
            data = client.responses.create(**request).model_dump()
        except openai.APIError as exc:
            # Only web search is switched off: the model itself may be fine for everything else.
            with self._lock:
                if "web_search" not in unsupported:
                    unsupported.add("web_search")
                    print(f"  {self.model('text')}: web search unavailable on {url} "
                          f"({getattr(exc, 'status_code', None) or type(exc).__name__}); using DuckDuckGo instead.")
            return None
        answer, citations, results = "", {}, []
        for item in data.get("output") or []:
            if item.get("type") == "web_search_call":
                results += [{"title": r.get("title") or "", "url": r.get("url") or "",
                             "snippet": (r.get("snippet") or "")[:300]} for r in item.get("results") or []]
            elif item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        answer += part.get("text") or ""
                        for note in part.get("annotations") or []:
                            if note.get("type") == "url_citation" and note.get("url"):
                                citations[note["url"]] = {"title": note.get("title") or "", "url": note["url"]}
        with self._lock:
            self.used_roles.add("text")
        return {"answer": answer.strip(), "data": parse_json(answer) if schema else None,
                "citations": list(citations.values()), "results": results}

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
