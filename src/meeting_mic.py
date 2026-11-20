"""The meeting's microphone, in a process of its own.

Started by src/meetings/recorder.py (MicSource) as `<app> --meeting-mic
[device]`. It opens the microphone and writes what it hears to stdout as
16-bit mono PCM; the first line on stderr is `ready <sample rate>`. It stops
when its stdin closes (the app closes it, or the app has gone).

Why a process: dictation keeps its own microphone stream in the app, and two
streams opened and closed in one process at the same moment (a meeting
toggled while a dictation starts) left dictation's stream dead. Two processes
are how two apps share a microphone every day.
"""
import sys


def main() -> int:
    import sounddevice as sd
    args = sys.argv[sys.argv.index("--meeting-mic") + 1:]
    device = args[0] if args and args[0] else None
    if device is not None and device.lstrip("-").isdigit():
        device = int(device)
    out = sys.stdout.buffer

    def callback(indata, frames, time_info, status):  # noqa: ARG001
        try:
            out.write(bytes(indata))
            out.flush()
        except (BrokenPipeError, ValueError, OSError):
            raise sd.CallbackAbort from None

    rate = 16000
    try:
        stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16",
                                   device=device, callback=callback, blocksize=1024)
    except Exception:
        rate = int(sd.query_devices(device, "input")["default_samplerate"])
        stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16",
                                   device=device, callback=callback, blocksize=1024)
    stream.start()
    sys.stderr.write(f"ready {rate}\n")
    sys.stderr.flush()
    try:
        sys.stdin.buffer.read()            # until the app closes our stdin
    except (OSError, KeyboardInterrupt):
        pass
    try:
        stream.stop()
        stream.close()
    except Exception:
        pass
    return 0
