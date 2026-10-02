"""Speech-to-text with Whisper running locally (mlx-whisper on Apple Silicon)."""
from __future__ import annotations

import logging

import numpy as np

from .config import Config

log = logging.getLogger(__name__)

# Whisper sometimes "hears" these in silence.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching", "bye", "."}


class Transcriber:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._backend = None
        self._fw_model = None

    def warm_up(self) -> None:
        """Load the model now so the first command isn't slow."""
        self.transcribe(np.zeros(16000, dtype=np.float32))

    def _prompt(self) -> str:
        words = [self.cfg.name, *self.cfg.vocabulary, *self.cfg.aliases.values()]
        seen, uniq = set(), []
        for w in words:
            if w.lower() not in seen:
                seen.add(w.lower())
                uniq.append(w)
        return "Voice commands for a Mac, like: open Messages, quit Google Chrome, " \
               "find my resume, open Cursor with my project folder. Names: " + ", ".join(uniq[:60]) + "."

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: mono float32 at 16 kHz."""
        if self._backend is None:
            try:
                import mlx_whisper  # noqa: F401
                self._backend = "mlx"
            except ImportError:
                self._backend = "faster"
                log.warning("mlx-whisper not installed; falling back to faster-whisper (slower).")

        if self._backend == "mlx":
            import mlx_whisper
            out = mlx_whisper.transcribe(
                audio, path_or_hf_repo=self.cfg.stt_model, language=self.cfg.language,
                initial_prompt=self._prompt(), condition_on_previous_text=False,
                temperature=0.0, fp16=True,
            )
            text = out.get("text", "")
        else:
            from faster_whisper import WhisperModel
            if self._fw_model is None:
                self._fw_model = WhisperModel("small.en", device="cpu", compute_type="int8")
            segs, _ = self._fw_model.transcribe(audio, language=self.cfg.language,
                                                initial_prompt=self._prompt(), vad_filter=True)
            text = " ".join(s.text for s in segs)

        text = text.strip()
        if text.lower().strip(" .!?") in HALLUCINATIONS:
            return ""
        return text
