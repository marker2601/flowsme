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

    def model(self, role: str) -> str:
        return self.cfg.vision_model if role == "vision" else self.cfg.text_model

    def available(self, role: str = "text") -> bool:
        return self.enabled and role not in self.failed_roles

    def chat(self, prompt: str, *, role: str = "text", system: str | None = None,
             images: tuple[str, ...] | list[str] = (), max_tokens: int = 800, json_mode: bool = False,
             _plain: bool = False) -> str | None:
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
        if not _plain and self.cfg.reasoning_effort and "reasoning_effort" not in unsupported:
            extras["extra_body"] = {"reasoning_effort": self.cfg.reasoning_effort}
        if not _plain and json_mode and "json_mode" not in unsupported:
            extras["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(model=model, messages=messages, temperature=self.cfg.temperature,
                                                  max_tokens=max_tokens, **extras)
        except openai.BadRequestError as exc:
            if extras:
                text = self.chat(prompt, role=role, system=system, images=images, max_tokens=max_tokens, _plain=True)
                if text is not None:  # the plain request worked, so the extras were the problem
                    with self._lock:
                        unsupported.update({"extra_body": "reasoning_effort", "response_format": "json_mode"}[k]
                                           for k in extras)
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

    def chat_json(self, prompt: str, **kwargs):
        prompt += "\n\nReply with valid JSON only, no other text."
        for _ in range(2):  # small models now and then emit broken JSON; one retry fixes most of it
            data = parse_json(self.chat(prompt, json_mode=True, **kwargs))
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
