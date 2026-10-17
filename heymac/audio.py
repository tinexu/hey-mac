"""Microphone: wake-word detection (openWakeWord, runs locally) + command recording."""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Callable

import numpy as np

from .config import Config

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 1280            # 80 ms — the chunk size openWakeWord expects


class Microphone:
    """One always-open input stream shared by the wake-word detector and the recorder.

    Callbacks run on the audio worker thread — marshal to the UI thread yourself.
    """

    def __init__(self, cfg: Config,
                 on_wake: Callable[[], None],
                 on_level: Callable[[float], None],
                 on_utterance: Callable[[np.ndarray | None], None]):
        self.cfg = cfg
        self.on_wake = on_wake
        self.on_level = on_level
        self.on_utterance = on_utterance

        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._recording = False
        self._rec_frames: list[np.ndarray] = []
        self._rec_started = 0.0
        self._heard_speech = False
        self._last_speech = 0.0
        self._noise_floor = 200.0
        self._cooldown_until = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake_model = None
        self._stream = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        import sounddevice as sd

        if self.cfg.enable_wake_word:
            self._load_wake_model()

        device = self._pick_device(sd)
        info = sd.query_devices(device, kind="input")
        # Record at the mic's own sample rate. Asking CoreAudio for 16 kHz directly fails on
        # many mics (AirPods, USB/Bluetooth headsets) with "PaMacCore (AUHAL) err='-50'".
        self._in_rate = int(info["default_samplerate"]) or 48000
        self._in_channels = 1
        log.info("Mic: %s @ %d Hz", info["name"], self._in_rate)

        self._resampler = None
        if self._in_rate != RATE:
            import soxr
            self._resampler = soxr.ResampleStream(self._in_rate, RATE, 1, dtype="float32")
        self._pending = np.zeros(0, dtype=np.float32)

        blocksize = int(self._in_rate * FRAME / RATE)          # ~80 ms at the native rate
        try:
            self._stream = sd.InputStream(device=device, samplerate=self._in_rate, channels=1,
                                          dtype="float32", blocksize=blocksize, callback=self._callback)
        except sd.PortAudioError:
            # a few devices refuse mono — take their channel count and mix down
            self._in_channels = max(1, int(info["max_input_channels"]))
            self._stream = sd.InputStream(device=device, samplerate=self._in_rate,
                                          channels=self._in_channels, dtype="float32",
                                          blocksize=blocksize, callback=self._callback)
        self._stream.start()
        threading.Thread(target=self._worker, name="mic-worker", daemon=True).start()

    def _pick_device(self, sd):
        """Pick the mic: [speech] input_device from config if set, otherwise the Mac's built-in mic
        (so an iPhone / AirPods that macOS switched to doesn't get used by accident)."""
        inputs = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
        want = (self.cfg.input_device or "").lower().strip()
        if want:
            for i, d in inputs:
                if want in d["name"].lower():
                    return i
            log.warning("No input device matching %r — trying the built-in mic.", self.cfg.input_device)
        for i, d in inputs:
            name = d["name"].lower()
            if ("macbook" in name or "built-in" in name or "imac" in name) and "iphone" not in name:
                return i
        log.warning("Couldn't find a built-in mic — using the system default.")
        return None

    def stop(self) -> None:
        self._stop.set()
        if self._stream:
            self._stream.stop()
            self._stream.close()

    def _load_wake_model(self) -> None:
        try:
            import openwakeword
            from openwakeword.model import Model
        except ImportError:
            log.warning("openwakeword not installed — wake word disabled, hotkey still works.")
            return
        ww = self.cfg.wake_word
        if not os.path.exists(ww):
            # make sure the built-in models are on disk (first run only)
            try:
                paths = openwakeword.get_pretrained_model_paths("onnx")
                if not any(os.path.exists(p) for p in paths):
                    from openwakeword.utils import download_models
                    download_models()
            except Exception as e:  # noqa: BLE001
                log.warning("Couldn't download wake-word models: %s", e)
        self._wake_model = Model(wakeword_models=[ww], inference_framework="onnx")
        log.info("Wake word ready: %s", ww)

    # ------------------------------------------------------------- control
    def start_recording(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._rec_frames = []
            self._rec_started = time.time()
            self._heard_speech = False
            self._last_speech = time.time()
        # drop audio that's queued from before the trigger
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def cancel_recording(self) -> None:
        with self._lock:
            self._recording = False
            self._rec_frames = []
        self._cooldown_until = time.time() + 1.0

    @property
    def recording(self) -> bool:
        return self._recording

    # ------------------------------------------------------------ internals
    def _callback(self, indata, frames, time_info, status):  # sounddevice thread
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
        try:
            self._q.put_nowait(mono.astype(np.float32, copy=True))
        except queue.Full:
            pass

    def _frames(self):
        """Yield 16 kHz int16 frames of exactly FRAME samples, resampling as needed."""
        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._resampler is not None:
                chunk = self._resampler.resample_chunk(chunk)
            self._pending = np.concatenate([self._pending, chunk])
            while len(self._pending) >= FRAME:
                out, self._pending = self._pending[:FRAME], self._pending[FRAME:]
                yield (np.clip(out, -1.0, 1.0) * 32767).astype(np.int16)

    def _worker(self) -> None:
        for frame in self._frames():
            rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))

            if self._recording:
                self._handle_recording(frame, rms)
                continue

            # idle: track background noise + listen for the wake word
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
            if self._wake_model is None or time.time() < self._cooldown_until:
                continue
            scores = self._wake_model.predict(frame)
            if max(scores.values(), default=0) >= self.cfg.wake_threshold:
                log.info("Wake word detected %s", scores)
                self._wake_model.reset()
                self._cooldown_until = time.time() + 2.0
                self.start_recording()
                self.on_wake()

    def _handle_recording(self, frame: np.ndarray, rms: float) -> None:
        now = time.time()
        speech_threshold = max(self._noise_floor * 2.5, 350.0)
        self._rec_frames.append(frame)
        self.on_level(min(1.0, rms / 4000))

        if rms > speech_threshold:
            self._heard_speech = True
            self._last_speech = now

        elapsed = now - self._rec_started
        done = (
            (self._heard_speech and now - self._last_speech > self.cfg.silence_seconds)
            or elapsed > self.cfg.max_seconds
            or (not self._heard_speech and elapsed > 5.0)      # never said anything
        )
        if not done:
            return

        with self._lock:
            frames, heard = self._rec_frames, self._heard_speech
            self._recording = False
            self._rec_frames = []
        self._cooldown_until = now + 1.0
        if not heard:
            self.on_utterance(None)
            return
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        self.on_utterance(audio)
