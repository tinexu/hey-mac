"""Other AI providers (OpenAI, Gemini, Ollama, OpenAI-compatible) with a fake HTTP server."""
import json

import httpx
import pytest

from heymac import llm
from heymac.actions import ActionResult
from heymac.brain import TOOLS, Brain
from heymac.config import Config, load_config


class FakeMac:
    def __init__(self):
        self.calls = []

    def open_app(self, name):
        self.calls.append(("open_app", name))
        return ActionResult(True, f"Opened {name}")

    def save_aesthetic(self, name, overrides=None):
        self.calls.append(("save_aesthetic", name, overrides))
        return ActionResult(True, "saved")

    def lock_screen(self):
        self.calls.append(("lock_screen",))
        return ActionResult(False, "couldn't lock")


def server(replies, seen):
    """An httpx client whose 'server' returns `replies` in order and records each request."""
    def handle(request):
        seen.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        status, body = replies.pop(0)
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handle))


def cfg_for(provider, **kw):
    cfg = Config()
    cfg.ai_provider = provider
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def chat_reply(content=None, tool_calls=None, **extra):
    msg = {"role": "assistant", "content": content, **extra}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return 200, {"choices": [{"message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}]}


def call(id_, name, args, **extra):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}, **extra}


# ----------------------------------------------------------------- picking the provider
@pytest.mark.parametrize("provider,model,expected", [
    ("anthropic", "", ("anthropic", "claude-haiku-4-5")),
    ("openai", "", ("openai", llm.DEFAULT_MODELS["openai"])),
    ("Gemini", "", ("gemini", llm.DEFAULT_MODELS["gemini"])),
    ("ollama", "llama3.1:8b", ("ollama", "llama3.1:8b")),
    ("google", "", ("gemini", llm.DEFAULT_MODELS["gemini"])),
    ("local", "", ("ollama", llm.DEFAULT_MODELS["ollama"])),
    ("openai", "claude-haiku-4-5", ("openai", llm.DEFAULT_MODELS["openai"])),   # leftover Claude name
])
def test_resolve_provider(provider, model, expected):
    assert llm.resolve_provider(cfg_for(provider, ai_model=model)) == expected


def test_unknown_provider_explains():
    with pytest.raises(llm.LLMError, match="anthropic, openai"):
        llm.resolve_provider(cfg_for("skynet"))


def test_compatible_needs_model_and_url():
    with pytest.raises(llm.LLMError, match="model"):
        llm.make_llm(cfg_for("openai_compatible"))
    with pytest.raises(llm.LLMError, match="base_url"):
        llm.make_llm(cfg_for("openai_compatible", ai_model="x"))


def test_config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[ai]\nprovider = "ollama"\nmodel = "qwen3:8b"\nbase_url = "http://studio.local:11434/v1"\n')
    cfg = load_config(str(p))
    assert (cfg.ai_provider, cfg.ai_model, cfg.ai_base_url) == ("ollama", "qwen3:8b", "http://studio.local:11434/v1")


# ----------------------------------------------------------------- tool formats
def test_chat_tools_are_valid_and_clean():
    tools = llm.chat_tools(TOOLS)
    assert len(tools) == len(TOOLS)
    blob = json.dumps(tools)
    assert '"default"' not in blob
    by_name = {t["function"]["name"]: t["function"] for t in tools}
    assert "parameters" not in by_name["lock_screen"]                     # no empty objects
    assert by_name["save_aesthetic"]["parameters"]["properties"]["overrides"]["type"] == "string"


def test_responses_tools_shape():
    tools = llm.responses_tools(TOOLS)
    assert all(t["type"] == "function" and "name" in t and t["parameters"]["type"] == "object" for t in tools)


# ----------------------------------------------------------------- Gemini / Ollama (chat completions)
def test_gemini_tool_loop(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    seen = []
    sig = {"extra_content": {"google": {"thought_signature": "abc"}}}
    http = server([
        chat_reply(tool_calls=[call("c1", "open_app", {"name": "Spotify"}, **sig)]),
        chat_reply("Opened Spotify."),
    ], seen)
    mac = FakeMac()
    reply = Brain(cfg_for("gemini"), mac, client=http).ask("put on some music app")
    assert reply.text == "Opened Spotify." and mac.calls == [("open_app", "Spotify")]
    assert seen[0]["url"].startswith("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
    assert seen[0]["headers"]["authorization"] == "Bearer g-key"
    assert seen[0]["body"]["model"] == llm.DEFAULT_MODELS["gemini"]
    msgs = seen[1]["body"]["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[-2]["tool_calls"][0]["extra_content"] == sig["extra_content"]   # signature sent back
    assert msgs[-1] == {"role": "tool", "tool_call_id": "c1", "name": "open_app", "content": "OK: Opened Spotify"}


def test_ollama_needs_no_key_and_strips_thinking(monkeypatch):
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    seen = []
    http = server([chat_reply("<think>they want to lock</think>Locking now.")], seen)
    reply = Brain(cfg_for("ollama"), FakeMac(), client=http).ask("hello")
    assert reply.text == "Locking now."
    assert seen[0]["url"] == "http://localhost:11434/v1/chat/completions"


def test_failed_tool_is_reported_as_error(monkeypatch):
    seen = []
    http = server([chat_reply(tool_calls=[call("c1", "lock_screen", {})]), chat_reply("I couldn't lock it.")], seen)
    Brain(cfg_for("ollama"), FakeMac(), client=http).ask("lock")
    assert seen[1]["body"]["messages"][-1]["content"].startswith("ERROR:")


def test_overrides_json_string_is_parsed(monkeypatch):
    seen = []
    http = server([
        chat_reply(tool_calls=[call("c1", "save_aesthetic", {"name": "rainy", "overrides": '{"volume": 30}'})]),
        chat_reply("Saved."),
    ], seen)
    mac = FakeMac()
    Brain(cfg_for("ollama"), mac, client=http).ask("save this as rainy with volume 30")
    assert mac.calls == [("save_aesthetic", "rainy", {"volume": 30})]


def test_compatible_server_with_custom_key_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    seen = []
    http = server([chat_reply("hi")], seen)
    cfg = cfg_for("openai_compatible", ai_model="some/model", ai_base_url="https://openrouter.ai/api/v1/",
                  ai_api_key_env="OPENROUTER_API_KEY")
    Brain(cfg, FakeMac(), client=http).ask("hi")
    assert seen[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen[0]["headers"]["authorization"] == "Bearer or-key"


# ----------------------------------------------------------------- OpenAI (Responses API)
def test_openai_responses_tool_loop(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "o-key")
    seen = []
    http = server([
        (200, {"output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "open_app",
             "arguments": '{"name": "Notes"}', "status": "completed"}]}),
        (200, {"output": [{"type": "message", "id": "msg_1", "role": "assistant",
                           "content": [{"type": "output_text", "text": "Notes is open."}]}]}),
    ], seen)
    mac = FakeMac()
    reply = Brain(cfg_for("openai"), mac, client=http).ask("open notes")
    assert reply.text == "Notes is open." and mac.calls == [("open_app", "Notes")]
    first, second = seen[0]["body"], seen[1]["body"]
    assert seen[0]["url"] == "https://api.openai.com/v1/responses"
    assert first["model"] == llm.DEFAULT_MODELS["openai"] and first["store"] is False
    assert first["max_output_tokens"] >= 4000
    assert second["input"][-2] == {"type": "function_call", "call_id": "call_1", "name": "open_app",
                                   "arguments": '{"name": "Notes"}'}
    assert second["input"][-1] == {"type": "function_call_output", "call_id": "call_1", "output": "OK: Opened Notes"}
    assert not any(i.get("type") == "reasoning" for i in second["input"] if isinstance(i, dict))


# ----------------------------------------------------------------- friendly errors
def test_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    http = server([], [])
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY isn't set.*platform.openai.com"):
        Brain(cfg_for("openai"), FakeMac(), client=http).ask("hi")


@pytest.mark.parametrize("provider,status,body,match", [
    ("gemini", 401, {"error": {"message": "API key not valid"}}, "rejected the API key"),
    ("ollama", 404, {"error": {"message": "model 'qwen3:8b' not found"}}, "ollama pull qwen3:8b"),
    ("openai", 429, {"error": {"message": "quota"}}, "rate limit or out of credit"),
    ("gemini", 500, {"error": {"message": "boom"}}, "Gemini error 500"),
])
def test_http_errors_are_explained(monkeypatch, provider, status, body, match):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    http = server([(status, body)], [])
    with pytest.raises(llm.LLMError, match=match):
        Brain(cfg_for(provider), FakeMac(), client=http).ask("hi")


def test_ollama_not_running():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)
    http = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(llm.LLMError, match="Ollama isn't running"):
        Brain(cfg_for("ollama"), FakeMac(), client=http).ask("hi")


def test_switching_provider_on_reload_rebuilds(monkeypatch):
    seen = []
    http = server([chat_reply("one"), chat_reply("two")], seen)
    cfg = cfg_for("ollama")
    brain = Brain(cfg, FakeMac(), client=http)
    brain.ask("hi")
    first = brain.llm
    cfg.ai_model = "llama3.1:8b"            # config.toml saved with a new model
    brain.ask("hi again")
    assert brain.llm is not first and seen[1]["body"]["model"] == "llama3.1:8b"