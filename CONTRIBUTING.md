# Contributing

Thanks for wanting to help! Bug reports, new voice commands, new mascots and docs fixes are all welcome.

## Setup

You need an Apple Silicon Mac to run the app, but the tests run anywhere.

```bash
git clone https://github.com/tinexu/hey-mac && cd hey-mac
./scripts/setup.sh                 # macOS: creates .venv and installs everything
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest                   # ~0.5 s, uses a fake Mac
heymac --text "open messages"      # run one command without the mic or UI
```

## How the code is laid out

| File | What it does |
|---|---|
| `heymac/commands.py` | Instant commands matched with regex ("quit chrome"). No AI, no cost. |
| `heymac/brain.py` | Everything else: the AI with a fixed list of tools. |
| `heymac/llm.py` | Adapters for Claude, OpenAI, Gemini, Ollama and OpenAI-compatible servers. |
| `heymac/actions.py` | The `Mac` class: every action the assistant can take. |
| `heymac/aesthetics.py` | Presets (wallpaper, dark mode, Spotify, editor theme…), undo and save. |
| `heymac/critters.py` | The animal mascots, drawn with a small "pen" interface so they can be tested. |
| `heymac/lily.py` | The floating corner window (AppKit). |
| `heymac/audio.py`, `stt.py` | Microphone, wake word, Whisper. |

## Adding a voice command

1. Add the action to `Mac` in `actions.py`. Return an `ActionResult(ok, message)`.
2. If it's simple and predictable, add a regex in `commands.py`. Otherwise add a tool to `TOOLS` in `brain.py`
   (the tool name must match the `Mac` method name) and a line in the system prompt.
3. Add tests. Look at `tests/test_notes.py` for the pattern: parametrized phrases plus a fake system.

## Adding a mascot

Most people don't need code for this: a folder of pictures is a character (see "Make your own character" in the
README, and `examples/characters/ghost`). To add a built-in, drawn-in-code animal instead:

Write a `_yourcritter(pen, state, level, phase, perk)` function in `critters.py`, add it to `ANIMALS` and `DRAW`,
and the tests will check it draws in every state without leaving the window. Coordinates are y-up, centered,
about ±60 units. A quick way to preview is to render it with pycairo using the same pen methods.

## Ground rules

- **Safety:** the AI can only call the tools in `brain.py`. Please don't add a tool that runs arbitrary shell
  commands or AppleScript from the model.
- **Privacy:** keep things local where possible. If a feature sends something new to an API, say so in the README.
- **Style:** plain Python 3.11+, type hints where helpful, short functions, comments that explain *why*.
- Be kind. This project follows the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).