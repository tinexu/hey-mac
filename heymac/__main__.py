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
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _load_env()

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
            reply = Brain(cfg, mac).ask(args.text)
            print("[AI]", end=" ")
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
