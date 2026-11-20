"""The faster-whisper (CTranslate2) engine for src/local_stt.py: Windows and
Intel Macs.

Optional: nothing here imports `faster_whisper` until a model is opened, so the
app starts without the package. Segments come back in the same shape as the
cloud ones (`start`, `end`, `text`, `avg_logprob`, `no_speech_prob`) so
`meetings.transcribe.clean_segments` treats both alike.

The model is opened from its folder in the data folder (src/local_stt.py
downloads and checks it); this module never goes to the network. src/local_stt.py
also owns the one-at-a-time lock and the idle unload.

Device choice, per call:

* The NVIDIA card is used when its libraries load (cuBLAS 12 + cuDNN 9, from
  the `nvidia-*-cu12` wheels or a system install) AND it has comfortably more
  free memory than the model needs (`VRAM_NEED_MB` + `VRAM_HEADROOM_MB`):
  float16 if that fits, int8_float16 if only that fits.
* Otherwise the CPU (int8), without an error. On Windows a full card does not
  reliably fail, it spills into shared system memory and crawls, so the check
  before loading matters more than catching out-of-memory afterwards.
* A GPU out-of-memory during load or inference redoes that call on the CPU and
  keeps to the CPU for `GPU_COOLDOWN_S` instead of retrying every chunk.

`auto_model()` picks the model to download by hardware: `large-v3-turbo` on a
GPU with room, else on the CPU `small` (8+ logical cores and 12+ GB RAM) or
`base`. The same model then runs on whichever device is free.

CTranslate2 4.9 also has an experimental Apple GPU ("mps") device. It is not
used: on an M5 it returned a 62 s clip with a stretch of text in lower case and
without punctuation (beam 5, float16), and Apple Silicon has whisper.cpp.

Environment: `WAFFLER_WHISPER_DEVICE=cpu` (or the older `NATTER_WHISPER_DEVICE`)
never uses the GPU.
"""
from __future__ import annotations

import gc
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

AUTO_GPU_MODEL = "large-v3-turbo"
BEAM_SIZE = 5

# Approximate dedicated GPU memory (MB) one loaded model uses during a
# transcription with beam 5: (float16, int8_float16). base, small and
# large-v3-turbo were measured on an RTX 4070 SUPER (CTranslate2 4.9.0, Windows
# 11, per-process "Dedicated Usage" counter, peak over a 5 minute clip); tiny,
# medium and large-v3 are estimates scaled from those by model size.
VRAM_NEED_MB: dict[str, tuple[int, int]] = {
    "tiny": (350, 300),               # estimate
    "base": (450, 350),               # measured 403 / 339
    "small": (900, 650),              # measured 883 / 595
    "medium": (2500, 1500),           # estimate
    "large-v3": (4000, 2400),         # estimate
    "large-v3-turbo": (2500, 1450),   # measured 2419 / 1395
}
VRAM_HEADROOM_MB = 1024         # free memory wanted on top of the model's need
GPU_COOLDOWN_S = 300.0          # after an out-of-memory: CPU only for this long
VRAM_RECHECK_S = 60.0           # after "not enough free": look again this often

# The CUDA libraries CTranslate2 4.5+ loads on Windows. A missing cuDNN does not
# raise, it ends the process, so they are checked before the GPU is tried.
_WIN_CUDA_DLLS = ("cublas64_12.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll")

_cuda_ok: bool | None = None                     # do the CUDA libraries load? None: not tried
_gpu_retry_at = 0.0                              # _now() before which the GPU is not tried
_dll_dirs_added = False
_last: dict = {"cuda_error": "", "free_vram_mb": None}
_now = time.monotonic                            # replaced in tests


def available() -> bool:
    """Is faster-whisper installed? (Does not import it.)"""
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


# ---- hardware ---------------------------------------------------------------

def _add_nvidia_dll_dirs() -> None:
    """Let Windows find cuBLAS/cuDNN installed by pip (`nvidia-*-cu12` wheels)."""
    global _dll_dirs_added
    if _dll_dirs_added or sys.platform != "win32":
        return
    _dll_dirs_added = True
    spec = importlib.util.find_spec("nvidia")
    dirs = [b for root in (list(spec.submodule_search_locations)
                           if spec and spec.submodule_search_locations else [])
            for b in Path(root).glob("*/bin")]
    # The packaged app ships without NVIDIA's libraries (about 2 GB). It looks
    # for them in <data folder>/gpu, should they be put there.
    try:
        try:
            from data_paths import data_dir
        except ImportError:  # imported as src.local_stt_ct2
            from src.data_paths import data_dir
        gpu_dir = data_dir() / "gpu"
        if gpu_dir.is_dir():
            dirs.append(gpu_dir)
    except Exception:           # noqa: BLE001
        pass
    for bin_dir in dirs:
        try:
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        except OSError:
            pass


def _cuda_usable() -> tuple[bool, str]:
    """(worth trying the GPU?, why not)."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return False, "no CUDA device"
    except Exception as e:      # noqa: BLE001
        return False, f"CUDA check failed ({type(e).__name__})"
    if sys.platform == "win32":
        import ctypes
        _add_nvidia_dll_dirs()
        for name in _WIN_CUDA_DLLS:
            try:
                ctypes.WinDLL(name)
            except OSError:
                return False, f"{name} not found"
    return True, ""


def _free_vram_mb() -> int | None:
    """Free memory on the first NVIDIA card, in MB; None when it cannot be read."""
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            info = pynvml.nvmlDeviceGetMemoryInfo(pynvml.nvmlDeviceGetHandleByIndex(0))
            return int(info.free) // (1024 * 1024)
        finally:
            pynvml.nvmlShutdown()
    except Exception:           # noqa: BLE001 - not installed, or no driver: try nvidia-smi
        pass
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode == 0:
            return int(out.stdout.strip().splitlines()[0].strip())
    except Exception:           # noqa: BLE001
        pass
    return None


def _total_ram_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong)] + [
                    (n, ctypes.c_ulonglong) for n in (
                        "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                        "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]
            st = _MemoryStatus()
            st.dwLength = ctypes.sizeof(st)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullTotalPhys / 2 ** 30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:           # noqa: BLE001
        return None


def _cpu_auto_model() -> str:
    """`small` on a machine with 8+ logical cores and 12+ GB of RAM, else `base`."""
    ram = _total_ram_gb()
    # 12 GB machines report a little under 12 (firmware reserves some).
    return "small" if (os.cpu_count() or 1) >= 8 and ram is not None and ram >= 11.5 else "base"


def _fit(model_name: str, free_mb: int | None) -> str | None:
    """The GPU compute type that fits in `free_mb`, or None. Unknown free memory
    (no pynvml, no nvidia-smi) skips the check."""
    f16, i8f16 = VRAM_NEED_MB[model_name]
    if free_mb is None or free_mb >= f16 + VRAM_HEADROOM_MB:
        return "float16"
    if free_mb >= i8f16 + VRAM_HEADROOM_MB:
        return "int8_float16"
    return None


def _is_oom(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return isinstance(exc, MemoryError) or "out of memory" in text or "out_of_memory" in text \
        or "alloc" in text


def _cpu_only() -> bool:
    return any(os.environ.get(name, "").strip().lower() == "cpu"
               for name in ("WAFFLER_WHISPER_DEVICE", "NATTER_WHISPER_DEVICE"))


def _plan(model_name: str, loaded=()) -> tuple[str, str]:
    """(device, compute type) for this call. `loaded` holds the (device,
    compute type) pairs already in memory."""
    global _cuda_ok, _gpu_retry_at
    cpu = ("cpu", "int8")
    if _cpu_only():
        _last["cuda_error"] = "WAFFLER_WHISPER_DEVICE=cpu"
        return cpu
    if _cuda_ok is False or _now() < _gpu_retry_at:
        return cpu
    for key in loaded:                      # already on the card: no new memory needed
        if key[0] == "cuda":
            return key
    if model_name not in VRAM_NEED_MB:
        return cpu
    # Memory first: the library check below maps ~180 MB of CUDA DLLs into the
    # process, which is wasted when the card is full anyway.
    free = _free_vram_mb()
    _last["free_vram_mb"] = free
    compute_type = _fit(model_name, free)
    if not compute_type:
        _gpu_retry_at = _now() + VRAM_RECHECK_S
        _last["cuda_error"] = (
            f"GPU memory: {free} MB free, '{model_name}' wants "
            f"{VRAM_NEED_MB[model_name][1] + VRAM_HEADROOM_MB} MB or more; using the CPU")
        return cpu
    if _cuda_ok is None:
        _cuda_ok, why = _cuda_usable()
        _last["cuda_error"] = why
        if not _cuda_ok:
            return cpu
    _last["cuda_error"] = ""                # the GPU is being used: no fallback in force
    return "cuda", compute_type


def auto_model() -> str:
    """The model to download for this machine: `large-v3-turbo` when an NVIDIA
    card can take it, else `small` or `base` for the CPU. Reads hardware, so
    src/local_stt.py asks once."""
    if sys.platform != "darwin" and not _cpu_only():
        try:
            ok, _why = _cuda_usable()
            if ok and _fit(AUTO_GPU_MODEL, _free_vram_mb()):
                return AUTO_GPU_MODEL
        except Exception:       # noqa: BLE001
            pass
    return _cpu_auto_model()


def status() -> dict:
    """Why the GPU was not used, if it was not. Reads no hardware."""
    return {"fallback_reason": _last["cuda_error"], "free_vram_mb": _last["free_vram_mb"],
            "gpu_retry_in_s": max(0, round(_gpu_retry_at - _now()))}


# ---- the engine ---------------------------------------------------------------

class Ct2Engine:
    """One model folder, opened on the device that is free at each call."""

    def __init__(self, folder: Path, name: str):
        self._folder = Path(folder)
        self._name = name
        self._models: dict[tuple[str, str], object] = {}    # (device, compute type) → model
        self.device = None                                  # what the last call ran on
        # Opened now, on the device that is free now, so that loading is not
        # counted as (or felt in) the first transcription.
        device, compute_type = _plan(name)
        try:
            self._open(device, compute_type)
        except Exception as e:              # noqa: BLE001
            if device != "cuda":
                raise
            self._gpu_failed(e)
            self._open(*_plan(name))
        self.device = next(iter(self._models))[0]

    def _open(self, device: str, compute_type: str):
        key = (device, compute_type)
        if key in self._models:
            return self._models[key]
        self.close()                        # one model in memory at a time
        from faster_whisper import WhisperModel
        kwargs = {"device": device, "compute_type": compute_type}
        if device == "cpu":
            kwargs["cpu_threads"] = min(8, max(1, (os.cpu_count() or 4) // 2))
        model = WhisperModel(str(self._folder), local_files_only=True, **kwargs)    # no network
        self._models[key] = model
        return model

    def _run(self, device: str, compute_type: str, audio: np.ndarray, language, prompt) -> list[dict]:
        model = self._open(device, compute_type)
        segments, _info = model.transcribe(
            audio, language=language or None, initial_prompt=prompt or None, vad_filter=False,
            beam_size=BEAM_SIZE, temperature=0.0, condition_on_previous_text=False)
        out = [{"start": float(s.start), "end": float(s.end), "text": s.text,
                "avg_logprob": float(s.avg_logprob), "no_speech_prob": float(s.no_speech_prob)}
               for s in segments]           # a generator: the work happens here
        self.device = device
        return out

    def transcribe(self, audio, language, prompt) -> list[dict]:
        """16 kHz mono float32 → raw Whisper segments, computed on this machine."""
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        device, compute_type = _plan(self._name, tuple(self._models))
        if device == "cuda":
            try:
                return self._run(device, compute_type, audio, language, prompt)
            except Exception as e:          # noqa: BLE001 - any GPU failure means: use the CPU
                self._gpu_failed(e)
                device, compute_type = _plan(self._name)    # now the CPU's choice
        return self._run(device, compute_type, audio, language, prompt)

    def _gpu_failed(self, exc: Exception) -> None:
        """Note why, free the card, and decide how long to stay away."""
        global _cuda_ok, _gpu_retry_at
        self.close()
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"
        if _is_oom(exc):
            _gpu_retry_at = _now() + GPU_COOLDOWN_S
            _last["cuda_error"] = f"GPU out of memory ({reason}); CPU for {int(GPU_COOLDOWN_S)} s"
        else:
            _cuda_ok = False                # not a memory problem: it would fail every time
            _last["cuda_error"] = reason

    def close(self) -> None:
        """Drop the loaded model and give the memory back."""
        n = len(self._models)
        for model in list(self._models.values()):
            try:                            # frees VRAM/RAM now, not at the next gc
                model.model.unload_model()
            except Exception:               # noqa: BLE001 - fakes, older CTranslate2
                pass
        self._models.clear()
        if n:
            gc.collect()
