"""On-device speech to text with faster-whisper (CTranslate2).

Optional: nothing here imports `faster_whisper` until a chunk is actually
transcribed, so the app starts without the package. Segments come back in the
same shape as the cloud ones (`start`, `end`, `text`, `avg_logprob`,
`no_speech_prob`) so `transcribe.clean_segments` treats both alike.

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

Memory: one model is held at a time, and it is released after
`idle_unload_s` (default 5 minutes) without a transcription; the next call
loads it again. `unload()` does the same on demand.

`model_name="auto"` picks by hardware: `large-v3-turbo` on a GPU with room,
else on the CPU `small` (8+ logical cores and 12+ GB RAM) or `base`.

A model already on disk is opened without touching the network; only a model
that has never been downloaded goes to Hugging Face, once.

Environment: `NATTER_WHISPER_DEVICE=cpu` never uses the GPU;
`NATTER_WHISPER_IDLE_S=<seconds>` sets the idle period (0: never unload).
"""
from __future__ import annotations

import gc
import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
AUTO = "auto"
DEFAULT_MODEL = "small"
AUTO_GPU_MODEL = "large-v3-turbo"

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
DEFAULT_IDLE_UNLOAD_S = 300.0

# The CUDA libraries CTranslate2 4.5+ loads on Windows. A missing cuDNN does not
# raise, it ends the process, so they are checked before the GPU is tried.
_WIN_CUDA_DLLS = ("cublas64_12.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll")

_lock = threading.Lock()
_models: dict[tuple[str, str, str], object] = {}     # (model, device, compute type) → model
_cuda_ok: bool | None = None                     # do the CUDA libraries load? None: not tried
_gpu_retry_at = 0.0                              # _now() before which the GPU is not tried
_dll_dirs_added = False
_idle_timer: threading.Timer | None = None
_last_used = 0.0
_last: dict = {"device": None, "compute_type": None, "model": None, "load_s": None,
               "transcribe_s": None, "cuda_error": "", "free_vram_mb": None,
               "unloaded": ""}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


_idle_unload_s = _env_float("NATTER_WHISPER_IDLE_S", DEFAULT_IDLE_UNLOAD_S)
_now = time.monotonic                            # replaced in tests


class LocalWhisperError(Exception):
    """A failure with a message fit to show the user."""


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
    # for them in <data folder>/gpu, where scripts/install_gpu_support.py puts them.
    try:
        from .paths import data_dir
        gpu_dir = data_dir() / "gpu"
        if gpu_dir.is_dir():
            dirs.append(gpu_dir)
    except Exception:
        pass
    for bin_dir in dirs:
        if True:
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


def _plan(requested: str) -> tuple[str, str, str]:
    """(model, device, compute type) for this call. Holds `_lock`."""
    global _cuda_ok, _gpu_retry_at
    auto = requested == AUTO
    gpu_model = AUTO_GPU_MODEL if auto else requested
    cpu = ((_cpu_auto_model() if auto else requested), "cpu", "int8")
    if os.environ.get("NATTER_WHISPER_DEVICE", "").strip().lower() == "cpu":
        _last["cuda_error"] = "NATTER_WHISPER_DEVICE=cpu"
        return cpu
    if _cuda_ok is False or _now() < _gpu_retry_at:
        return cpu
    for key in _models:                     # already on the card: no new memory needed
        if key[0] == gpu_model and key[1] == "cuda":
            return key
    # Memory first: the library check below maps ~180 MB of CUDA DLLs into the
    # process, which is wasted when the card is full anyway.
    free = _free_vram_mb()
    _last["free_vram_mb"] = free
    compute_type = _fit(gpu_model, free)
    if not compute_type:
        _gpu_retry_at = _now() + VRAM_RECHECK_S
        _last["cuda_error"] = (
            f"GPU memory: {free} MB free, '{gpu_model}' wants "
            f"{VRAM_NEED_MB[gpu_model][1] + VRAM_HEADROOM_MB} MB or more; using the CPU")
        return cpu
    if _cuda_ok is None:
        _cuda_ok, why = _cuda_usable()
        _last["cuda_error"] = why
        if not _cuda_ok:
            return cpu
    _last["cuda_error"] = ""                # the GPU is being used: no fallback in force
    return gpu_model, "cuda", compute_type


# ---- loading and unloading ----------------------------------------------------

def _load_model(model_name: str, device: str, compute_type: str):
    """Build one WhisperModel. No network when the model is on disk."""
    from faster_whisper import WhisperModel
    kwargs = {"device": device, "compute_type": compute_type}
    if device == "cpu":
        kwargs["cpu_threads"] = min(8, max(1, (os.cpu_count() or 4) // 2))
    try:
        return WhisperModel(model_name, local_files_only=True, **kwargs)    # no network
    except Exception:                       # noqa: BLE001
        if _is_cached(model_name):
            raise                           # on disk and still failed: a real load error
        try:
            return WhisperModel(model_name, **kwargs)                       # first use: download
        except Exception as e:              # noqa: BLE001
            if device == "cuda":
                raise
            raise LocalWhisperError(
                f"Couldn't download the '{model_name}' speech model. It needs the internet once; "
                f"after that it works offline.") from e


def _open(model_name: str, device: str, compute_type: str):
    """The loaded model for this key; anything else loaded is released first, so
    one model is held at a time. Holds `_lock`."""
    key = (model_name, device, compute_type)
    if key in _models:
        return _models[key]
    _release()
    t0 = time.perf_counter()
    model = _load_model(model_name, device, compute_type)
    _last["load_s"] = round(time.perf_counter() - t0, 3)
    _models[key] = model
    return model


def _release() -> int:
    """Drop every loaded model and give the memory back. Holds `_lock`."""
    n = len(_models)
    for model in list(_models.values()):
        try:                                # frees VRAM/RAM now, not at the next gc
            model.model.unload_model()
        except Exception:                   # noqa: BLE001 - fakes, older CTranslate2
            pass
    _models.clear()
    if n:
        gc.collect()
    return n


def unload() -> bool:
    """Release the loaded model (VRAM and RAM). The next transcription loads it
    again. True when something was loaded."""
    global _idle_timer
    with _lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None
        released = _release() > 0
        if released:
            _last["unloaded"] = "on request"
        return released


def set_idle_unload(seconds: float | None) -> None:
    """Idle period after which the model is released; 0 or None: keep it loaded."""
    global _idle_unload_s
    with _lock:
        _idle_unload_s = float(seconds or 0)
        _arm_idle_timer()


def _arm_idle_timer(delay: float | None = None) -> None:
    """(Re)start the idle countdown. Holds `_lock`."""
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None
    if _idle_unload_s > 0 and _models:
        _idle_timer = threading.Timer(_idle_unload_s if delay is None else delay, _idle_fire)
        _idle_timer.daemon = True
        _idle_timer.name = "natter-whisper-idle"
        _idle_timer.start()


def _idle_fire() -> None:
    """Timer thread: unload if nothing was transcribed for the idle period. It
    waits for the lock, so it never pulls a model from under a transcription."""
    global _idle_timer
    with _lock:
        if threading.current_thread() is not _idle_timer:
            return                          # superseded while waiting for the lock
        _idle_timer = None
        if not _models or _idle_unload_s <= 0:
            return
        remaining = _idle_unload_s - (time.monotonic() - _last_used)
        if remaining > 0.01:
            _arm_idle_timer(remaining)
            return
        _release()
        _last["unloaded"] = "idle"


# ---- transcription --------------------------------------------------------------

def _run(model, audio: np.ndarray, language: str) -> list[dict]:
    segments, _info = model.transcribe(
        audio, language=language or None, vad_filter=False, beam_size=5, temperature=0.0,
        condition_on_previous_text=False)
    return [{"start": float(s.start), "end": float(s.end), "text": s.text,
             "avg_logprob": float(s.avg_logprob), "no_speech_prob": float(s.no_speech_prob)}
            for s in segments]              # a generator: the work happens here


def _gpu_failed(exc: Exception) -> None:
    """Note why, free the card, and decide how long to stay away. Holds `_lock`."""
    global _cuda_ok, _gpu_retry_at
    _release()
    reason = f"{type(exc).__name__}: {str(exc)[:200]}"
    if _is_oom(exc):
        _gpu_retry_at = _now() + GPU_COOLDOWN_S
        _last["cuda_error"] = f"GPU out of memory ({reason}); CPU for {int(GPU_COOLDOWN_S)} s"
    else:
        _cuda_ok = False                    # not a memory problem: it would fail every time
        _last["cuda_error"] = reason


def transcribe_local(audio: np.ndarray, model_name: str = DEFAULT_MODEL,
                     language: str = "") -> list[dict]:
    """16 kHz mono float32 → raw Whisper segments, computed on this machine."""
    global _last_used
    requested = (model_name or DEFAULT_MODEL).strip()
    if requested != AUTO and requested not in MODELS:
        raise LocalWhisperError(
            f"Unknown on-device model '{requested}'. Choose one of: "
            f"{', '.join((AUTO,) + MODELS)}.")
    if not available():
        raise ImportError("faster_whisper is not installed")
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    with _lock:
        try:
            name, device, compute_type = _plan(requested)
            if device == "cuda":
                try:
                    return _transcribe(name, device, compute_type, audio, language)
                except Exception as e:      # noqa: BLE001 - any GPU failure means: use the CPU
                    _gpu_failed(e)
                    name, device, compute_type = _plan(requested)   # now the CPU's choice
            return _transcribe(name, device, compute_type, audio, language)
        finally:
            _last_used = time.monotonic()
            _arm_idle_timer()


def _transcribe(name: str, device: str, compute_type: str, audio: np.ndarray,
                language: str) -> list[dict]:
    model = _open(name, device, compute_type)
    t1 = time.perf_counter()
    out = _run(model, audio, language)
    _last.update(device=device, compute_type=compute_type, model=name,
                 transcribe_s=round(time.perf_counter() - t1, 3), unloaded="")
    return out


def _repo_dir(model_name: str) -> Path | None:
    """Where the Hugging Face cache keeps this model, whether or not it is there."""
    try:
        from faster_whisper.utils import _MODELS
        repo = _MODELS.get(model_name)
    except Exception:           # noqa: BLE001
        repo = None
    if not repo:
        return None
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.join(Path.home(), ".cache", "huggingface"), "hub")
    return Path(hub) / ("models--" + repo.replace("/", "--"))


def _is_cached(model_name: str) -> bool:
    d = _repo_dir(model_name)
    try:
        return bool(d) and any((d / "snapshots").glob("*/model.bin"))
    except OSError:
        return False


def status() -> dict:
    """For the Settings page: installed? which device ran last? is a model in
    memory? why was the GPU not used? Reads no hardware, so it is cheap to poll."""
    installed = available()
    loaded = sorted(_models)
    return {
        "installed": installed,
        "device": _last["device"],                    # 'cuda', 'cpu' or None (nothing run yet)
        "compute_type": _last["compute_type"],
        "model": _last["model"],
        "load_s": _last["load_s"],
        "transcribe_s": _last["transcribe_s"],
        "loaded": bool(loaded),                       # is a model in memory right now?
        "loaded_on": loaded[0][1] if loaded else None,
        "unloaded": _last["unloaded"],                # 'idle', 'on request' or ''
        "idle_unload_s": _idle_unload_s,
        "free_vram_mb": _last["free_vram_mb"],        # as last read before a GPU load, or None
        "fallback_reason": _last["cuda_error"],       # why the GPU was not used, if it was not
        "cuda_error": _last["cuda_error"],            # (the same, under its earlier name)
        "gpu_retry_in_s": max(0, round(_gpu_retry_at - _now())),
        "models": list(MODELS),
        "models_cached": [m for m in MODELS if _is_cached(m)] if installed else [],
        "models_loaded": sorted({k[0] for k in loaded}),
    }
