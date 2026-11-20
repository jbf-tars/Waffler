"""The whisper.cpp engine for src/local_stt.py (Apple Silicon).

``pywhispercpp`` carries whisper.cpp built with Metal, so the model runs on
the Mac's GPU. Nothing is imported until a model is opened. One model file
(GGML), opened by its path: pywhispercpp's own downloader and model folder
are never used.
"""
from __future__ import annotations

import gc
import math
import platform
import sys
from pathlib import Path

BEAM_SIZE = 5


def _quiet_target():
    """Where whisper.cpp's start-up lines go while a model is opened: nowhere,
    when this process has a real stderr to turn away for a moment. A windowed
    app can have none, and pywhispercpp's redirect needs one (it calls
    ``sys.stderr.flush()``), so there it is left alone."""
    try:
        sys.stderr.flush()
        sys.stderr.fileno()
        return None             # pywhispercpp: None sends them to the null device
    except Exception:           # noqa: BLE001 - no stderr, or one that is not a file
        return False            # no redirection


class WhisperCppEngine:
    def __init__(self, model_file: Path):
        from pywhispercpp.model import Model
        self.device = "metal" if (sys.platform == "darwin" and platform.machine() == "arm64") else "cpu"
        # Strategy 1 is beam search. The printing switches are whisper.cpp's
        # own console output, which nobody reads here.
        self._model = Model(str(model_file), params_sampling_strategy=1,
                            redirect_whispercpp_logs_to=_quiet_target(),
                            print_progress=False, print_realtime=False, print_timestamps=False)

    def transcribe(self, audio, language, prompt) -> list:
        """16 kHz mono float32 → segments. Parameters stay set on the model
        between calls, so every one that matters is given every time."""
        segments = self._model.transcribe(
            audio, extract_probability=True,
            language=language or "auto", initial_prompt=prompt or "",
            temperature=0.0, no_context=True, translate=False,
            beam_search={"beam_size": BEAM_SIZE, "patience": -1.0})
        out = []
        for s in segments:
            seg = {"start": s.t0 / 100.0, "end": s.t1 / 100.0, "text": s.text}
            p = getattr(s, "probability", float("nan"))
            if isinstance(p, float) and math.isfinite(p) and p > 0:
                seg["avg_logprob"] = math.log(p)
            out.append(seg)
        return out

    def close(self) -> None:
        self._model = None      # the context is freed when the model is collected
        gc.collect()
