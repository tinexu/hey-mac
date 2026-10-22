#!/usr/bin/env bash
# One-time setup. Run from the project folder:  ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname)" != "Darwin" ]]; then echo "This only runs on macOS."; exit 1; fi
command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }

PY=$(command -v python3.12 || command -v python3.11 || true)
if [[ -z "$PY" ]]; then
  echo "Installing Python 3.12 via Homebrew…"; brew install python@3.12; PY=$(brew --prefix)/bin/python3.12
fi
brew list portaudio >/dev/null 2>&1 || brew install portaudio

"$PY" -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

python - <<'PY'
from openwakeword.utils import download_models
download_models()
print("Wake-word models downloaded.")
PY

[[ -f .env ]] || { cp .env.example .env; echo ">>> Put your API key in .env (Claude by default; see config.toml [ai] for OpenAI, Gemini or Ollama)"; }
mkdir -p ~/.config/heymac
[[ -f ~/.config/heymac/config.toml ]] || cp config.example.toml ~/.config/heymac/config.toml
echo
echo "Done! Start it with:   source .venv/bin/activate && heymac"