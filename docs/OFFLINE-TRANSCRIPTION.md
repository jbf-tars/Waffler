# Speech to text on this computer

Waffler can turn speech into text on the computer itself: no key, no network
once the model is downloaded. One setting decides, for dictation and for
meetings alike. This page says what was built, why the engines were chosen,
what was measured, what the Settings page needs to call, what a release build
needs, and what is not done.

Status in one paragraph: the engine, the model download, the routing for
dictation and meetings, the `Api` methods and the packaging lines are written
and tested. **There is no Settings UI and no setup-wizard step for it yet**, on
purpose (the pages are being restyled by someone else); until those exist the
only way to switch it on is to call the `Api` methods or edit `settings.json`.
Nothing changes for anyone who does not switch it on: with no
`transcription_mode` saved, Waffler behaves exactly as before.

## The setting

`settings.json` in the data folder:

| key | values | meaning |
| --- | --- | --- |
| `transcription_mode` | `"cloud"` (also what a missing value means) or `"local"` | Who turns speech into text, for dictation **and** meetings. |
| `local_stt_model` | a name from the table of models below | Optional. Absent: the model already downloaded, else the one that suits the machine. For diagnosis; the page does not need it. |

Rules that follow from "on this computer":

- **Local never falls back to the cloud.** If the model is missing or fails,
  the dictation is kept as a Not sent card (as for any failed dictation) and a
  meeting keeps its audio; nothing is uploaded, even when keys are present.
- The mode is read again for every recording, so a change applies to the next
  dictation without a restart.
- Saving `"cloud"` with no Groq or OpenAI key is refused with a sentence.
- With `"local"` saved, Waffler starts and listens with no key at all. The
  clean-up, Transforms, Command mode and meeting notes still need a key (see
  "Without a key" below).
- The old `LOCAL_WHISPER=1` environment switch is gone. It chose between
  mlx-whisper and faster-whisper `base`, neither of which a shipped build ever
  contained, and it lost to a Groq key anyway.

The model is stored in `<data folder>/models/<name>/` (`src/data_paths.py`, so
`~/.waffler-hosted/models/` in an installed app, a temporary folder in tests).
It is never in the app bundle or the repository.

## Which engine, and why

Three were compared: mlx-whisper, faster-whisper (CTranslate2) and whisper.cpp
(through the `pywhispercpp` bindings).

**Decision: whisper.cpp on Apple Silicon, faster-whisper on Windows and Intel
Macs.** One engine everywhere was the aim, and neither candidate manages it:

- *faster-whisper everywhere* fails on speed on Apple Silicon. CTranslate2 has
  no stable Apple GPU path, so it runs on the CPU, where the accurate model
  (large-v3-turbo) took 3.9 to 4.5 s for a 10 s dictation on an M5 against 0.5
  to 1.4 s for whisper.cpp on the GPU. Dictation is latency first. Dropping to
  `small` to get the time back costs accuracy (it was the only size to get
  words wrong in the test below), and it is still the slower of the two.
- *whisper.cpp everywhere* fails on wheels and on what is known. `pywhispercpp`
  has no current Intel Mac wheel (1.2.0 for Python 3.11 only, nothing for
  3.14), its Windows wheel is CPU only, and nothing here could measure it on
  Windows. faster-whisper on Windows already has, in this code base, a GPU path
  measured on an RTX card and a CPU path that CTranslate2 is known to be good
  at.

So the second engine earns its place: each runs where it is fast and where it
has wheels, and both sit behind one module with one interface.

mlx-whisper was as fast as whisper.cpp on the Mac but cannot be the choice: it
pulls in PyTorch, MLX, Numba, SciPy and SymPy (about 1.2 GB installed, against
about 3.5 MB for pywhispercpp), its model is 1.6 GB against 0.57 GB, and it is
Apple Silicon only.

### The comparison

| | mlx-whisper 0.4.3 | faster-whisper 1.2.1 (CTranslate2 4.9.0) | whisper.cpp 1.8.5 (pywhispercpp 1.5.1) |
| --- | --- | --- | --- |
| Runs on | Apple GPU (MLX) | CPU; NVIDIA GPU (CUDA) | Apple GPU (Metal); CPU elsewhere |
| Accuracy, short dictation | same models, same text in the test | same | same |
| Accuracy, long meeting | turbo: all figures and names right | turbo right; `small` got a phrase wrong on the experimental GPU device (see below) | turbo right; `small` got the same phrase wrong |
| Speed, 10 s clip, M5 (model loaded) | 0.47 s (turbo) | 3.9 to 4.5 s (turbo, CPU int8); 1.0 to 2.4 s (`small`) | 0.5 to 1.4 s (turbo, 5-bit) |
| Speed, 62 s clip, M5 | 1.8 s | 13 to 15 s (turbo); 4.1 to 7.6 s (`small`) | 1.9 to 4.2 s |
| Model download | 1.6 GB (turbo) | 1.62 GB (turbo), 486 MB (`small`), 148 MB (`base`) | 574 MB (turbo, 5-bit), 190 MB (`small`, 5-bit) |
| Python wheels, macOS arm64 | 3.11 yes, 3.14 yes (MLX) | 3.11 yes, 3.14 yes | 3.11 yes, 3.14 yes |
| Python wheels, macOS x86_64 | none (MLX is arm64 only) | 3.11 yes; 3.14 **no** (onnxruntime, a dependency, has no wheel for it there) | 3.11 old 1.2.0 only; 3.14 none |
| Python wheels, Windows x64 | MLX has them, untested | 3.11 yes, 3.14 yes | 3.11 yes, 3.14 yes (CPU only) |
| What it drags into the bundle | torch 634 MB, mlx 234 MB, llvmlite + numba 155 MB, scipy 99 MB, sympy 76 MB | ctranslate2, PyAV (46 MB on the Mac), tokenizers 10 MB, huggingface_hub 8 MB; onnxruntime 85 MB can be excluded | 3 MB of libraries, plus tqdm and platformdirs |
| PyInstaller | not tried (ruled out on size) | no hook for ctranslate2 or faster_whisper in pyinstaller-hooks-contrib: the spec collects ctranslate2's DLLs. **Not built here.** | follows the linked libraries by itself; checked with a frozen probe (below) |

"Accuracy" here is narrow: the clips were spoken by macOS `say`, which is clean,
evenly paced synthetic speech. It shows the engines agree and that `small` is
the weaker model; it does not show how any of them cope with a real voice, an
accent, a poor microphone or a noisy room. Published results put
large-v3-turbo close to large-v3 (what Groq runs for Waffler today) and
`small` and `base` clearly behind; that is the reason turbo is the default
wherever the hardware can run it quickly.

Two things that were not expected:

- CTranslate2 4.9 ships an experimental Apple GPU device (`mps`). It was about
  three times faster than the CPU for turbo (1.3 to 1.9 s for the 10 s clip),
  still slower than whisper.cpp, and on the 62 s clip with beam 5 it returned
  a stretch of text in lower case with no punctuation. It is not used.
- The first use of whisper.cpp by a newly built app is slow: 3.6 s in one
  frozen build, 10 to 13 s in three others and 36 s in a newly made Python
  environment on a busy machine, against 1 to 3 s for every start after. The cause was not established (most likely macOS checking the new
  libraries and Metal preparing its shaders once per build), and a signed,
  notarised build was not measured. So the model is loaded in
  the background when Waffler starts in local mode, when local mode is
  chosen, and when a download finishes (`local_stt.warm()`), rather than at the
  first dictation.

### The models

`src/local_stt.py` `MODELS`. Every file is pinned to a commit of its Hugging
Face repository with its size and SHA-256; a download that does not match is
thrown away.

| name | engine | download | default on |
| --- | --- | --- | --- |
| `large-v3-turbo-q5` | whisper.cpp | 574 MB | Apple Silicon |
| `small-q5` | whisper.cpp | 190 MB | (only if named in `local_stt_model`) |
| `large-v3-turbo` | faster-whisper | 1.62 GB | Windows with an NVIDIA card that has room for it |
| `small` | faster-whisper | 486 MB | Windows / Intel Mac, CPU, 8+ cores and 12+ GB RAM |
| `base` | faster-whisper | 148 MB | Windows / Intel Mac, smaller machines |

One model is in use at a time: the one named in `local_stt_model`, else one
already downloaded, else the default for the machine. The choice on Windows
(`local_stt_ct2.auto_model`) is the rule the meetings engine already had.

## What was measured

Machine: Apple M5, 16 GB, macOS 26.4, Python 3.14.4. It was in use the whole
time (other builds and test runs kept one to three cores busy), so these are
the times of a laptop doing other things, not best cases. The same clip
varied by up to a factor of two from run to run; where a range is given, it is
the range seen.

Clips: spoken by macOS `say`, converted with `afconvert` to 16 kHz mono. Short:
10.1 s, 36 words. Long: 62.5 s, about 190 words of a meeting with names,
dates and sums of money.

Through `src/local_stt.py`, exactly as the app calls it (beam 5), with
`scripts/local_stt_check.py`. Every row is a real download from Hugging Face
into a temporary data folder, cancelled at 20%, resumed from the part-file,
checked against the pinned size and SHA-256, run, and removed:

| engine, model | download | first load | 10 s clip, model loaded | 62 s clip | words right, long clip |
| --- | --- | --- | --- | --- | --- |
| whisper.cpp, `large-v3-turbo-q5` (Metal) | 574 MB | 0.4 to 0.6 s | 0.7 to 1.4 s | 2.5 to 4.2 s | 94% |
| whisper.cpp, `small-q5` (Metal) | 190 MB | 0.1 s | 0.5 s | 2.1 s | 93% |
| faster-whisper, `base` (CPU, int8) | 148 MB | 0.3 s | 0.7 s | 3.2 s | 93% |
| faster-whisper, `small` (CPU, int8) | 486 MB | 0.6 s | 2.4 s | 7.6 s | 93% |
| faster-whisper, `large-v3-turbo` (CPU, int8) | 1.62 GB | 1.8 s | 4.4 s | 14.1 s | 94% |

The short clip came back word for word from all five ("three" written "3").
"Words right" counts the spoken words found in order in the text; "£8,400" for
"eight thousand four hundred pounds" counts against it, so 100% is not
reachable on the long clip.

The default on this Mac, in the words of the brief: the model is **574 MB**,
the first load takes **about half a second**, and a **10 s clip takes 0.7 to
1.4 s** once loaded (1.3 to 1.4 s including the first load). Text returned for
the short clip:

> Hi Sarah, thanks for sending the draft over. I had a look this morning and I
> think the second section needs a clearer example. Could we meet on Thursday
> at 3 to go through it together?

Also seen:

- The 574 MB download took 17 to 24 s on this connection.
- Two callers at once (a dictation and a meeting chunk, the same 10 s clip)
  both got their text in 1.3 to 2.2 s in all; the second waits for the first.
- Idle unload and reload worked on both engines.
- Under heavier load (an Xcode build and a simulator on the same machine)
  faster-whisper `small` on the CPU took 3.4 to 8.8 s for the 10 s clip and
  45 s for the 62 s clip: six times its quiet figure. whisper.cpp on the GPU
  moved less (about twice, at the machine's busiest: next point). A laptop in a
  meeting is a busy laptop, which is one more reason for the GPU engine where
  there is one.
- Python 3.11 (the release build's version): a fresh 3.11 environment made
  from `requirements.txt` on this Mac installed pywhispercpp and not
  faster-whisper, as the markers intend; the new tests and
  `scripts/local_stt_check.py` pass in it. Run back to back with 3.14 the
  times were the same. Those runs were made with the machine at its busiest
  (an iOS simulator and a test run using the GPU and every core): both
  Pythons then took 1.6 to 3.2 s to load the model, 1.0 to 2.6 s for the 10 s
  clip and 5 to 10 s for the 62 s clip, about twice the table above. The very first use in that new Python
  took 36 s to load (see "not expected" above).
- A first pass with each engine's own API (before `local_stt` existed) gave
  the same picture: whisper.cpp turbo 0.5 to 0.8 s, mlx-whisper turbo 0.47 s,
  faster-whisper turbo on the CPU 3.9 to 4.5 s, all for the 10 s clip. In that
  pass `small` wrote "still and paid off to 45 days" for "still unpaid after
  forty five days" in two runs (whisper.cpp `small`, and CTranslate2 `small` on
  the experimental GPU device); the turbo models never did.
- Silence: whisper.cpp returned "Thank you." for five seconds of digital
  silence, as Whisper does everywhere. Dictation's existing filters drop it
  (tested), and meetings do not send chunks with no voice.

Not measured: any real voice, any language but English, Windows, an Intel
Mac, an NVIDIA card, an M1 or an 8 GB Mac, memory use, battery.

## What the page calls

All on `window.pywebview.api`. None of them raises; each returns `ok`.

| method | returns |
| --- | --- |
| `local_stt_status()` | `{ok, mode, available, engine, model, label, state, percent, bytes_done, bytes_total, size_on_disk, error, cancelled, loaded, device, load_s, transcribe_s}`. Cheap: poll it while a download runs. |
| `local_stt_download()` | Starts the download on a thread and returns the status at once (`started: true`, or `false` if one was already running). |
| `local_stt_cancel()` | `{ok, was_downloading}`. What arrived is kept; downloading again carries on from there. |
| `local_stt_remove()` | Deletes the model (stopping a download, releasing memory). Status plus `freed_bytes`. Leaves the mode alone. |
| `get_settings()` | now includes `transcription_mode` (`"cloud"` or `"local"`). |
| `save_settings({transcription_mode})` | Saves it, applies it to the running app, and loads the model in the background when it is `"local"`. Refuses an unknown value, and `"cloud"` with no key. |

Fields worth knowing:

- `state`: `"not_downloaded"`, `"downloading"` or `"ready"`.
- `percent`, `bytes_done`, `bytes_total`: of the whole model. After a cancel or
  a failure they show what is already on disk, so the bar restarts from there.
- `size_on_disk`: bytes in the model's folder, part-files included.
- `error`: a sentence fit to show, set when the last download failed
  (`""` otherwise). `cancelled`: the last download was stopped by the person.
- `available`: `false` when this build has no engine for this machine (an
  Intel Mac on Python 3.14, or a build made without the package). Hide or
  disable the choice then; `local_stt_download()` answers with a sentence.
- `loaded`, `device` (`"metal"`, `"cpu"`, `"cuda"`), `load_s`, `transcribe_s`:
  for an About line or diagnosis.

Progress without polling: define
`window.waffler_local_stt_changed = function (status) { ... }`. It is called
with the same object as the percentage moves, when the download ends, and
after a removal.

A sensible order for the page: `local_stt_status()`; if `state` is not
`"ready"`, offer the download with its size (`bytes_total`) and show
`percent`; once `"ready"`, `save_settings({transcription_mode: "local"})`.
Saving `"local"` before the model is ready is allowed (dictations are then
kept as Not sent cards, with Try again in the Journal, and the existing
automatic resend tries each of them up to three more times), but the page
should not do it that way round.

Two things the current page does on its own, with no change to it:

- `get_settings().transcription_backend` answers `"mlx"` on a Mac and
  `"faster"` on Windows in local mode. Those are the two names `ui/logic.js`
  already has for "on this Mac" and "on this computer", so About and the
  privacy line say so instead of naming a provider that hears nothing. They
  no longer name an engine. When the page is restyled it can read
  `transcription_mode` instead and those two ids can go.
- A meeting with no key shows its transcript, and the line below in place of
  its notes.

For the setup wizard (not built): a first run with no key can finish by
calling `local_stt_download()`, then `save_settings({transcription_mode:
"local"})`, then `complete_setup()`. `get_onboarding_status().needs_setup` is
already false for a finished setup in local mode with no key, and the wizard's
practice dictation (`wizard_start_hotkey_test`) already runs without a key in
local mode and uses the local model. Its screens still ask for a key first;
that is the part to build.

## How it is wired

- `src/local_stt.py`: the one module. `is_available()`, `model_status()`,
  `download(progress_cb)`, `start_download()`, `cancel_download()`,
  `remove_model()`, `transcribe(audio, language=None)`,
  `transcribe_segments()`, `warm()`, `unload()`, and the setting
  (`mode_of`, `mode_is_local`, `settings_problem`). It imports no engine until
  one is needed. One model in memory, loaded at first use, released after five
  minutes idle (`WAFFLER_LOCAL_STT_IDLE_S`). One transcription at a time; a
  dictation waiting goes ahead of a meeting chunk waiting.
- `src/local_stt_whispercpp.py`, `src/local_stt_ct2.py`: the two engines.
  The second is the old `src/meetings/local_whisper.py`, moved and cut down to
  an engine (its GPU checks, measured memory needs and CPU fallback are kept);
  the meetings engine no longer has an implementation of its own.
- Dictation: `WhisperTranscriber` (`src/transcribe_whisper.py`) reads the mode
  at the start of every `transcribe_sync` and sends the audio to
  `local_stt.transcribe` instead of a provider. Everything around it is
  unchanged and still runs: quiet-speech lift, silence trimming, splitting of
  long takes, the hallucination filters, the Vocabulary (as the words to
  listen for). Provenance: `_backend` is `"local"`, `_last_cloud_provider` is
  `"local"`, usage is recorded under provider `local`, the speech deadline is
  the longer local one, and the cross-provider retry is skipped.
- Meetings: `src/meetings/transcribe.py` sends each chunk to
  `local_stt.transcribe_segments(..., background=True)` when the mode is
  local, and maps a failure to a sentence. The audio is kept, so "retranscribe"
  works after the model arrives.
- `local_stt` registers itself under both names it can be imported by
  (`local_stt` and `src.local_stt`). Without that the app and the meetings
  engine would each have loaded their own copy of the model.

## Without a key

What works with `transcription_mode: "local"` and no key at all:

- Dictation. The words are pasted as the model heard them (Whisper already
  punctuates and capitalises). The clean-up has no model to ask, so it is
  treated exactly like clean-up Off: no "no key" message after every
  dictation, no warning flag on the Journal card.
- Meetings: recording and the transcript.

What does not, and what the person sees:

- Meeting notes and titles. The meeting finishes (status `ready`, never left
  on "Writing the notes") with its transcript and this line where the notes
  would be: *"The transcript is saved. Writing notes needs an API key or
  Waffler Plus: add a key in Settings and the notes can be written then."*
  Adding a key later and asking for the notes again writes them from the
  saved transcript.
- Asking a question about a meeting: *"Answering questions needs an API key or
  Waffler Plus. Add a key in Settings."*
- Clean-up styles, Transforms and Command mode: unchanged; they already say
  when no key is set.

"Waffler Plus" is only a name in those two sentences. Nothing here implements
it.

## Could the notes be written on this computer too?

Not built. An assessment, with what is and is not measured.

What notes ask of a model is harder than transcription: read up to an hour of
transcript (about 9,000 words, 12,000 tokens), keep every figure and name
right, invent nothing, and write a structured page. Small models are worst at
exactly the two things the notes prompt insists on (no invented facts, correct
numbers), and a long transcript must be condensed in parts first
(`llm._split_parts` already does this for small context windows), which
multiplies the time.

Candidates that fit a laptop (4-bit GGUF or MLX weights; sizes are download
sizes, speeds are typical published figures for Apple Silicon, **not measured
here**):

| model | size | rough speed, M-series | fit for notes |
| --- | --- | --- | --- |
| Gemma 3n / Gemma 4 "e2b" class (2 B effective) | 2 to 4 GB | 40 to 80 tokens/s | The meetings engine already prefers `gemma4:e2b`, then `gemma4:e4b`, for a local endpoint, with a comment that they measured best for notes at their size. Usable for a short meeting; drops detail on a long one. |
| Gemma 4 "e4b" class (4 B effective) | 5 to 10 GB | 20 to 40 tokens/s | Noticeably better notes; needs 16 GB of RAM to sit beside everything else. |
| Qwen 3 4B, Phi-4-mini (3.8 B) | 2.5 to 3 GB | 30 to 60 tokens/s | Similar class; structured output is good, faithfulness on long input is the weak point. |
| Llama 3.2 3B | 2 GB | 40 to 70 tokens/s | Fast, the weakest of these at keeping figures straight. |
| 7 to 8 B models (Llama 3.1 8B, Qwen 3 8B) | 4.5 to 5 GB | 15 to 30 tokens/s | The first size that is dependable for notes; too heavy for an 8 GB Mac. |

At 30 tokens/s, 500 tokens of notes take about 17 s to write, after the model
has read the transcript: about 12,000 tokens for an hour, at a few hundred
tokens per second, so one to two minutes more, and several minutes on an 8 GB
machine or a CPU-only Windows laptop. That is acceptable for notes (they are
written after the meeting, in the background) and not for anything live.

How it would be bundled, and why none of the three ways is free:

1. **The person's own Ollama** (no bundling). The meetings engine already
   speaks to an OpenAI-compatible endpoint and to Ollama's own API
   (`llm_provider: "custom"`, `custom_base_url`, context sizes, thinking off),
   and this change keeps that path working. What is missing is only a way to
   set those two values from Waffler. A few hours of work. The cost is that
   the person installs and runs Ollama themselves.
2. **llama.cpp inside Waffler** (`llama-cpp-python`). PyPI has no binary
   wheels for it on any of our platforms (checked: macOS arm64 and Windows x64,
   Python 3.11 and 3.14), so the release workflows would have to compile it
   (CMake, Metal on the Mac, MSVC on Windows) or take wheels from the
   project's own index. Then a second model download of 2 to 5 GB, memory
   management next to the speech model, and the same download, verify and
   remove flow built here. Days of work plus a new thing to keep building.
3. **MLX** (`mlx-lm`): Apple Silicon only, and it brings the same heavy
   dependencies that ruled out mlx-whisper.

**Recommendation.** Do not bundle a local LLM now. Ship local transcription
with the honest line above, and let Waffler Plus (or a free Groq key) write
the notes: a hosted model is better at this job than anything that fits in 4
GB, and notes are the part of the product where a wrong figure costs trust.
If notes with no account matter, do option 1 first (a "local model address"
field: small, already half built, and it lets the result be judged on real
meetings with `gemma4:e4b`, which is on this Mac already). Bundle llama.cpp
only if that trial shows the notes are good enough to put the Waffler name
on. Whatever is chosen, measure it on real meeting transcripts before
deciding: the numbers in the table above are not ours.

## What a release build needs

Changed, not run (the release build scripts were not run):

- `requirements.txt` (macOS): `pywhispercpp>=1.5.1` on arm64;
  `faster-whisper>=1.2.0` on Intel below Python 3.14.
- `requirements_windows.txt`: `faster-whisper>=1.2.0`.
- `Waffler_mac.spec`: hidden imports for whichever engine is installed in the
  build's Python, pywhispercpp's package metadata (it reads its own version
  at import), `onnxruntime` excluded.
- `Waffler_windows.spec`: hidden imports for faster-whisper and what it
  imports at load (`ctranslate2`, `tokenizers`, `huggingface_hub`, `av`),
  ctranslate2's DLLs collected into its package folder, `onnxruntime` and
  `pywhispercpp` excluded.
- The model is never bundled: the specs add no model file, and the model
  folder is under the data folder.

Checked: a small frozen program (PyInstaller 6.22, Python 3.14, macOS arm64),
built from a throwaway spec made of the on-device block copied out of
`Waffler_mac.spec` around a script that only imports `src/local_stt.py`. It
found the downloaded model, ran it on Metal and returned the right text; with
the warm-up taking the slow first use (12 s in that build), the dictation
after it took 1.1 s. PyInstaller collected `libwhisper` and every `libggml-*`
by following the extension module's links. The libraries' minimum macOS is
11.0, below the app's 14.0. `tests/test_local_stt_packaging.py` pins the
requirements and runs both specs' on-device blocks with PyInstaller's helpers
replaced.

Not checked, and worth a look before a release:

- The real `Waffler_mac.spec` build, on Python 3.11, signed and notarised
  (hardened runtime with Metal; first-use time of a signed build).
- **Anything on Windows.** The Windows spec lines follow how ctranslate2 loads
  its DLLs but have not been built or run. The faster-whisper engine itself
  was run here on the Mac's CPU, which exercises the same Python code but not
  Windows, CUDA or the frozen app.
- Bundle size on Windows (PyAV and ctranslate2 are the large parts).
- `scripts/check_macos_minos.py` on the built app.
- CI installs the new requirements on both runners; the test suite does not
  need the engines (they are replaced in tests), but `pip install` now
  downloads them.

## Checks

- `tests/test_local_stt.py` (the module: status, download failure, retry,
  resume, damage, cancel, one-at-a-time, dictation before meeting, idle
  unload, the two engines with their packages replaced),
  `tests/test_local_dictation.py`, `tests/test_local_meetings.py`,
  `tests/test_local_stt_api.py`, `tests/test_local_stt_packaging.py`. Nothing
  in them downloads or loads a model, and they pass with no engine installed.
- `scripts/local_stt_check.py`: the real thing. Downloads the model into a
  temporary data folder, cancels and resumes, transcribes two spoken clips,
  runs two callers at once, waits for the idle unload, removes the model.
  `--engine faster-whisper` runs the other engine. Not part of the suite.

## Not done

- The Settings UI and the setup-wizard step (deliberately).
- Any test on Windows, on an Intel Mac, with an NVIDIA card, or of a real
  release build.
- Accuracy on real speech. Only synthetic speech was measured.
- A local LLM for notes (assessed above, not built).
- A way to pick a different model from the page (`local_stt_model` works but
  nothing sets it), and cleaning up a model left behind when the selection
  changes: `local_stt_remove()` removes the selected one only.
- Languages: `language` from Settings is passed to the model and `auto` lets
  it decide, as with the providers, but only English was run.
- The 5-bit turbo model was not compared with the full-precision one beyond
  the two clips (same text on both).
- README, CHANGELOG and the Privacy page do not mention any of this yet. They
  should when there is something to click: in local mode no audio leaves the
  computer, which changes what the Privacy page can say.
- A dictation that arrives while a piece of a meeting is being transcribed
  waits for that piece (up to 28 s of audio). On the Mac's GPU that is about a
  second. On a slow Windows CPU it could be many seconds; the fix would be to
  let a dictation interrupt the piece and run it again afterwards.
- On a slow CPU a very long take can outrun the local speech deadline (twice
  the cloud one): at the speed of real time, a 20 minute take takes 20 minutes
  and the deadline is 20.5.
- `gpu` folder for NVIDIA libraries: `local_stt_ct2` looks for CUDA DLLs in
  `<data folder>/gpu` as the old code looked in `<data folder>/meetings/gpu`;
  nothing in this repository puts them there.
