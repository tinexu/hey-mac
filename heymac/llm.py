"""Talk to whichever AI you picked: Claude, OpenAI, Gemini, Ollama (local) or any OpenAI-compatible server.

config.toml:

  [ai]
  provider = "anthropic"     # anthropic | openai | gemini | ollama | openai_compatible
  model = ""                 # empty = a good cheap default for that provider (see DEFAULT_MODELS)
  base_url = ""              # only for ollama (if not on this Mac) or openai_compatible
  api_key_env = ""           # only if your key lives in a differently named variable

Every provider gets the same tools and system prompt; brain.py runs the loop:
  turn = llm.create(system, messages, tools)  →  run turn.calls  →  llm.add_results(...)  →  repeat
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

PROVIDERS = ("anthropic", "openai", "gemini", "ollama", "openai_compatible")

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-6-luna",
    "gemini": "gemini-3.8-flash",
    "ollama": "qwen3:8b",
    "openai_compatible": "",
}
KEY_ENV = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "ollama": [],                                     # local, no key
    "openai_compatible": ["OPENAI_API_KEY"],
}
BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "ollama": "http://localhost:11434/v1",
}
KEY_HELP = {
    "anthropic": "https://console.anthropic.com/",
    "openai": "https://platform.openai.com/api-keys",
    "gemini": "https://aistudio.google.com/apikey",
}
NAMES = {"anthropic": "Claude", "openai": "OpenAI", "gemini": "Gemini", "ollama": "Ollama",
         "openai_compatible": "your AI server"}


class LLMError(RuntimeError):
    """A problem the user can fix (missing key, Ollama not running…). The message is shown as-is."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str
    calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None                    # the provider's own assistant message, sent back verbatim


def resolve_provider(cfg) -> tuple[str, str]:
    """(provider, model) from config, with friendly defaults."""
    provider = str(getattr(cfg, "ai_provider", "anthropic") or "anthropic").lower().replace("-", "_")
    provider = {"claude": "anthropic", "google": "gemini", "chatgpt": "openai", "local": "ollama",
                "compatible": "openai_compatible"}.get(provider, provider)
    if provider not in PROVIDERS:
        raise LLMError(f'Unknown AI provider "{provider}". Use one of: {", ".join(PROVIDERS)}.')
    model = str(getattr(cfg, "ai_model", "") or "").strip()
    # someone switched provider but left the old Claude model name in their config
    if provider != "anthropic" and model.startswith("claude"):
        model = ""
    model = model or DEFAULT_MODELS[provider]
    if not model:
        raise LLMError("Set [ai] model in config.toml (the model name your server uses).")
    return provider, model


def api_key(provider: str, cfg=None) -> str:
    names = [cfg.ai_api_key_env] if cfg is not None and getattr(cfg, "ai_api_key_env", "") else KEY_ENV[provider]
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    if provider == "ollama" or (provider == "openai_compatible" and not names):
        return "ollama"
    where = f" Get one at {KEY_HELP[provider]}" if provider in KEY_HELP else ""
    raise LLMError(f"{names[0]} isn't set. Put it in your .env file.{where}")


def make_llm(cfg, client=None):
    """The right adapter for config.toml's [ai] section. `client` lets tests inject a fake."""
    provider, model = resolve_provider(cfg)
    max_tokens = int(getattr(cfg, "max_tokens", 600) or 600)
    if provider == "anthropic":
        return AnthropicLLM(model, max_tokens, client=client, cfg=cfg)
    base = (getattr(cfg, "ai_base_url", "") or BASE_URLS.get(provider, "")).rstrip("/")
    if not base:
        raise LLMError('Set [ai] base_url in config.toml, e.g. "http://localhost:1234/v1".')
    if provider == "openai" and base == BASE_URLS["openai"]:
        # OpenAI's own API: Responses API, where its newest models land first.
        # Reasoning tokens count toward the limit, so leave room.
        return OpenAIResponsesLLM(model, max(max_tokens, 4000), base, provider, cfg=cfg, http=client)
    return ChatCompletionsLLM(model, max_tokens, base, provider, cfg=cfg, http=client)


# ---------------------------------------------------------------- tools in each format
def _clean_schema(schema: dict, loose: bool) -> dict:
    """OpenAI-style providers: drop `default` and free-form objects some of them reject."""
    out = {}
    for k, v in schema.items():
        if loose and k == "default":
            continue
        if k == "properties":
            props = {}
            for pk, pv in v.items():
                if loose and pv.get("type") == "object" and not pv.get("properties"):
                    # e.g. save_aesthetic.overrides: pass it as a JSON string instead
                    props[pk] = {"type": "string",
                                 "description": (pv.get("description", "") + " (as a JSON object string)").strip()}
                else:
                    props[pk] = _clean_schema(pv, loose)
            out[k] = props
        elif isinstance(v, dict):
            out[k] = _clean_schema(v, loose)
        else:
            out[k] = v
    return out


def chat_tools(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        fn = {"name": t["name"], "description": t["description"]}
        schema = _clean_schema(t["input_schema"], loose=True)
        if schema.get("properties"):                   # some servers reject empty parameter objects
            fn["parameters"] = schema
        out.append({"type": "function", "function": fn})
    return out


def responses_tools(tools: list[dict]) -> list[dict]:
    out = []
    for t in tools:
        schema = _clean_schema(t["input_schema"], loose=True)
        schema.setdefault("properties", {})
        out.append({"type": "function", "name": t["name"], "description": t["description"], "parameters": schema})
    return out


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


THINK = re.compile(r"<think>.*?</think>", re.S)


def _clean_text(text) -> str:
    return THINK.sub("", text or "").strip()        # small local models sometimes think out loud


# ---------------------------------------------------------------- Anthropic (Claude)
class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, model, max_tokens, client=None, cfg=None):
        self.model, self.max_tokens, self._client, self.cfg = model, max_tokens, client, cfg

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key("anthropic", self.cfg))
        return self._client

    def create(self, system: str, messages: list, tools: list[dict]) -> Turn:
        resp = self.client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                           system=system, tools=tools, messages=messages)
        text = " ".join(b.text for b in resp.content if b.type == "text").strip()
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use":
            calls = []
        return Turn(text, calls, raw=resp.content)

    def add_turn(self, messages: list, turn: Turn) -> None:
        messages.append({"role": "assistant", "content": turn.raw})

    def add_results(self, messages: list, results: list[tuple[ToolCall, str, bool]]) -> None:
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": not ok}
            for c, out, ok in results]})


# ---------------------------------------------------------------- HTTP helper
class _HTTP:
    def __init__(self, base, provider, cfg=None, http=None):
        self.base, self.provider, self.cfg, self._http = base, provider, cfg, http

    @property
    def http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=60)
        return self._http

    def post(self, path: str, body: dict) -> dict:
        import httpx
        headers = {"Authorization": f"Bearer {api_key(self.provider, self.cfg)}", "Content-Type": "application/json"}
        try:
            r = self.http.post(f"{self.base}{path}", json=body, headers=headers)
        except httpx.ConnectError:
            if self.provider == "ollama":
                raise LLMError("Ollama isn't running. Open the Ollama app (or run `ollama serve`).") from None
            raise LLMError(f"Couldn't reach {NAMES[self.provider]} at {self.base}.") from None
        except httpx.TimeoutException:
            raise LLMError(f"{NAMES[self.provider]} took too long to answer.") from None
        if r.status_code >= 400:
            raise LLMError(self._explain(r))
        return r.json()

    def _explain(self, r) -> str:
        try:
            err = r.json().get("error", r.text)
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        except ValueError:
            msg = r.text
        name = NAMES[self.provider]
        if r.status_code in (401, 403):
            return f"{name} rejected the API key ({msg[:120]}). Check your .env."
        if r.status_code == 404 and self.provider == "ollama":
            model = getattr(self, "model", "")
            return f"Ollama doesn't have that model yet. Run: ollama pull {model}"
        if r.status_code == 404:
            return f"{name} doesn't know that model ({msg[:120]}). Check [ai] model in config.toml."
        if r.status_code == 429:
            return f"{name} says you're over your rate limit or out of credit."
        if r.status_code == 400 and "tool" in msg.lower() and self.provider == "ollama":
            return f"This Ollama model can't use tools. Try: ollama pull {DEFAULT_MODELS['ollama']}"
        return f"{name} error {r.status_code}: {msg[:200]}"


# ---------------------------------------------------------------- Chat Completions (Gemini, Ollama, others)
class ChatCompletionsLLM(_HTTP):
    def __init__(self, model, max_tokens, base, provider, cfg=None, http=None):
        super().__init__(base, provider, cfg, http)
        self.model, self.max_tokens = model, max_tokens

    def create(self, system: str, messages: list, tools: list[dict]) -> Turn:
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system}] + messages,
                "tools": chat_tools(tools)}
        data = self.post("/chat/completions", body)
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = [ToolCall(tc.get("id") or f"call_{i}", tc["function"]["name"], _args(tc["function"].get("arguments")))
                 for i, tc in enumerate(msg.get("tool_calls") or []) if tc.get("function")]
        # send the message back exactly as received (Gemini needs its thought signatures), minus nulls
        raw = {k: v for k, v in msg.items() if v is not None}
        raw["role"] = "assistant"
        for i, tc in enumerate(raw.get("tool_calls") or []):
            tc.setdefault("id", calls[i].id if i < len(calls) else f"call_{i}")
            tc.setdefault("type", "function")
        return Turn(_clean_text(msg.get("content") if isinstance(msg.get("content"), str) else ""), calls, raw=raw)

    def add_turn(self, messages: list, turn: Turn) -> None:
        messages.append(turn.raw)

    def add_results(self, messages: list, results: list[tuple[ToolCall, str, bool]]) -> None:
        for c, out, ok in results:
            messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name,
                             "content": out if ok else f"ERROR: {out}"})


# ---------------------------------------------------------------- OpenAI Responses API
class OpenAIResponsesLLM(_HTTP):
    def __init__(self, model, max_tokens, base, provider="openai", cfg=None, http=None):
        super().__init__(base, provider, cfg, http)
        self.model, self.max_tokens = model, max_tokens

    def create(self, system: str, messages: list, tools: list[dict]) -> Turn:
        body = {"model": self.model, "instructions": system, "input": messages,
                "tools": responses_tools(tools), "max_output_tokens": self.max_tokens, "store": False}
        data = self.post("/responses", body)
        output = data.get("output") or []
        texts, calls = [], []
        for item in output:
            if item.get("type") == "message":
                texts += [c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text"]
            elif item.get("type") == "function_call":
                calls.append(ToolCall(item.get("call_id") or item.get("id"), item["name"], _args(item.get("arguments"))))
        text = " ".join(t for t in texts if t).strip() or (data.get("output_text") or "")
        return Turn(_clean_text(text), calls, raw=output)

    def add_turn(self, messages: list, turn: Turn) -> None:
        # send the function calls (and any text) back in order. Nothing is stored on OpenAI's side
        # (store=False), so drop server ids and reasoning items, which would need server storage.
        for item in turn.raw:
            if item.get("type") in ("function_call", "message"):
                messages.append({k: v for k, v in item.items() if k not in ("id", "status")})

    def add_results(self, messages: list, results: list[tuple[ToolCall, str, bool]]) -> None:
        for c, out, ok in results:
            messages.append({"type": "function_call_output", "call_id": c.id, "output": out if ok else f"ERROR: {out}"})