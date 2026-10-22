"""python -m heymac  [--config path] [--text "open messages"] [--debug]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _load_env() -> None:
    for p in (Path.cwd() / ".env", Path.home() / ".config" / "heymac" / ".env"):
        if p.is_file():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def main() -> None:
    ap = argparse.ArgumentParser(prog="heymac", description="Voice assistant for your Mac")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--text", help="run one command without the UI/mic (for testing), e.g. --text 'quit chrome'")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--list-mics", action="store_true", help="show microphones and their sample rates")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _load_env()

    if args.list_mics:
        import sounddevice as sd
        default_in = sd.default.device[0]
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                star = "*" if i == default_in else " "
                print(f"{star} [{i}] {d['name']}  ({int(d['default_samplerate'])} Hz)")
        return

    from .config import load_config
    cfg = load_config(args.config)

    if args.text:
        from .actions import Mac
        from .brain import Brain, Reply
        from .commands import route
        mac = Mac(cfg)
        action = route(args.text, mac)
        if action:
            r = action()
            reply = Reply(r.message, r.items, r.ok)
            print("[fast path]", end=" ")
        else:
            from .llm import LLMError, resolve_provider
            try:
                reply = Brain(cfg, mac).ask(args.text)
            except LLMError as e:
                reply = Reply(str(e), ok=False)
            print(f"[AI: {' / '.join(resolve_provider(cfg))}]" if reply.ok else "[AI]", end=" ")
        print(reply.text)
        for i, p in enumerate(reply.items, 1):
            print(f"  {i}. {p}")
        return

    if sys.platform != "darwin":
        sys.exit("heymac only runs on macOS.")
    from .app import run
    run(cfg)


if __name__ == "__main__":
    main()