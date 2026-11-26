# Speech to text on this computer

Waffler can turn speech into text on the computer itself: no key, no network
once the model is downloaded. One setting decides, for dictation and for
meetings alike. This page says what was built, why the engines were chosen,
what was measured, what the Settings page needs to call, what a release build
needs, and what is not done.

Status in one paragraph: the engine, the model download, the routing for
dictation and meetings, the `Api` methods and the packaging lines are written
and tested. It is chosen in two places: **setup's first step** ("Download the
free model and get to work", see "First run" below) and **Settings, Keys and
providers** (`ui/howitruns.js`). Nothing changes for anyone who does not
choose it: with no `transcription_mode` saved, Waffler behaves exactly as
before.

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
| `local_stt_status()` | `{ok, mode, available, engine, model, label, state, percent, bytes_done, bytes_total, size_on_disk, error, cancelled, loaded, device, load_s, transcribe_s, suits_machine, machine_reason}`. Cheap: poll it while a download runs. |
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
- `suits_machine`, `machine_reason`: is this computer a good one for it?
  `local_stt.machine_verdict()`, by the table in "The models": Apple Silicon
  (`"apple_silicon"`); Windows with an NVIDIA card that has room for the large
  model (`"nvidia"`); Windows or an Intel Mac with 8+ logical cores and 12+ GB
  of memory (`"cpu"`); anything smaller, or a machine that could not be read,
  is `false` with `"small_machine"` (it works, with the `base` model, slowly).
  The hardware is read once. `get_onboarding_status()` also returns
  `transcription_mode`, so setup knows which way a half-finished setup is on.

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

## First run

Setup's first step (`ui/index.html` `#obStepConnect` in its `choose` state,
`ui/app.js` `wizChooseLocal` and friends, the decisions in `ui/logic.js`
`setupLocalView` and the functions beside it) is two cards:

- **On this computer. Free.** One download (its size from `bytes_total`),
  then no key, no account and no internet. "Download and start" calls
  `local_stt_download()`; the card shows a progress bar with the percentage
  and "Stop the download" (`local_stt_cancel()`), and after a stop or a
  failure "Carry on downloading" or "Try again", which carry on from the
  part already here. Setup carries on while it downloads: Continue is
  enabled as soon as the download is running.
- **With your own key. Faster.** The Groq screens, unchanged, reached from
  that card (Back returns to the two cards).

The card that suits the machine comes first and is marked "Recommended for
this computer" (`suits_machine`). Where it does not suit, the key is first
and the free card says it will be slow there; where `available` is false the
free card says so and its button is disabled.

When `transcription_mode` is saved (`logic.js` `setupModeToSave`): at once
when there is no key (so a reopened setup knows the choice), and only when
the model is ready when a key already works (so dictation never stops in
between). A key that passes its check after the free way was tried saves
`"cloud"` again and stops the download.

After the first step: the stepper reads "On this computer"; Try it waits for
the model with one line and the progress, then starts the practice by itself;
the last step keeps "Open TextEdit and try it" disabled until the model is
here; Done is allowed meanwhile, and the main window says when the model has
arrived. A setup reopened past the first step (a Mac "Quit & Reopen") carries
the download on by itself; on the first step it offers "Carry on downloading".
Nothing in Settings asks someone on the free way to add a key: the clean-up
reads "Off, typed as you said it".

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

With the writing model downloaded as well (the optional second download, below)
none of the limits in this section apply: dictations are tidied, and notes and
questions are answered, on this computer. Without it:

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

"Waffler Plus" was only a name in those two sentences when they were written.
It exists now, for an account that has it (`src/account.py`): with Plus the
chosen way the account service does the work, with no key and no model here.
A dictation goes to `POST /api/dictate`; a meeting's audio to
`POST /api/plus/transcribe`, in parts of twelve minutes once the recording has
ended (`src/meetings/recorder.py` `finish_waiting`); notes, Ask, Transforms and
Command mode send their own prompts to `POST /api/plus/write`. Neither
sentence is shown to someone on Plus.

## Tidying and notes on this computer

Built, as an optional second download: `src/local_llm.py` (the module),
`src/local_llm_llamacpp.py` (the engine), and the clean-up, notes and Ask
wired to it. Off until downloaded. Measured on 21 November 2026 on an M5 with
16 GB; nothing else was measured.

**What it does.** With the writing model downloaded and no key: a dictation is
tidied by it (the app's own prompt and guards, inside the clean-up's own
deadline; too slow and the words are pasted as said, as with a provider), and
meeting notes and Ask are written by it. With a key nothing changes unless
`writing_mode: "local"` is saved (a tick box in Settings once both are
there); then the model here is asked alone, and a failure is never passed to
the key. Transforms and Command mode still need a key.

**The runtime: llama.cpp, through `llama-cpp-python`.** One runtime and one
model format (GGUF) for the Mac (Metal) and Windows, inside the app's own
process, with nothing for the person to install. MLX is Apple Silicon only
and brings the dependencies that ruled out mlx-whisper. The cost: PyPI has no
binary wheel (it is compiled; see the release section), and the project's own
wheel index served three Metal wheels that would not unzip (0.3.34 to 0.3.36,
"bad zipfile offset"), so the source build is the only one that worked.

**The model: Google's own 4-bit build of Gemma 4 E4B** (`google/gemma-4-E4B-
it-qat-q4_0-gguf`, Apache 2.0, 5.15 GB), pinned to a commit with its size and
SHA-256. Three were tried, each over the same 28 dictations (rambling, lists
said aloud, an email, names and numbers, swearing, one-liners; 27 go to the
model, the short one never does) and three transcripts (258, 1,418 and 5,441
words, written for this test, with 15, 26 and 60 facts, decisions and actions
to find). Everything was read by hand as well as counted.

| model | download | memory, loaded | a 60-word dictation | notes: 258 / 1,418 / 5,441 words | found | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Gemma 4 E4B, 4-bit | 5.15 GB | 3.6 GB | 1.8 to 2.0 s | 11 / 22 / 84 s | 15 / 26 / 54 | **Chosen.** |
| Gemma 4 E2B, 4-bit | 3.35 GB | 1.9 GB | 1.4 to 2.0 s | 7 / 19 / 39 s | 15 / 25 / 47 | Added a sign-off nobody said ("Thanks.", "Thanks again, James") to 24 of 27 dictations; notes gave a date that was not said and actions to the wrong people. |
| LFM3 1.7B, 4-bit | 1.10 GB | 1.6 GB | never finished one | 5 / 9 / 41 s | 15 / 23 / 46 | Wrote its reasoning in place of the text; notes added years and swapped owners. |

Memory is the process's resident size with a 16k-token window (the model's
file is mapped, about 2.4 GB of it; 1.2 GB is its own). Loading takes about
2.5 s with the file in the disk cache, and reading the clean-up's long prompt
once takes 3.5 s; both are done ahead of time (`warm()` at start-up, after
the download, and again as a recording starts if the model was released).

**What the measurements changed.** Each of these is in the code with its
reason:

- *Clean-up, E4B, the app's prompt unchanged:* nothing invented and no
  meaning changed in the 27. Three flaws, all with lists: one came back
  numbered 3, 2, 3; one was written along one line (at temperature 0.1 only);
  and the sentence leading into a list ("Right, three things for today.") was
  left out in 3 list dictations of 10. So: temperature 0 on this computer
  (the same text in any order, which 0.1 did not give), a numbered list that
  does not count up is renumbered, inline items go on their own lines, and a
  dropped lead-in is put back from the speaker's own words
  (`OpenAIStyler._tidy_numbered_list`, `_restore_list_lead_in`; on-device
  answers only). Still not done by it: "I need eggs, milk, bread and apples"
  stays a sentence where the prompt asks for bullets, and a counted list
  ("the three things are ...") comes back as lines without the dashes.
- *Notes, E4B, the app's prompt unchanged:* not good enough. On the
  1,418-word meeting, "the twenty-eighth" became "28 January" in 3 runs of 7,
  and actions were given to "Owner", "Speaker" or the wrong person in most.
  Two rules more for this model only (`LOCAL_NOTES_RULES` in
  `src/meetings/llm.py`: no month, year or weekday that was not said; name who
  does an action only when it is certain) and temperature 0: 3 runs of 3 with
  nothing invented, 26 of 26 found, owners right. Providers are sent the
  prompt they always were.
- It left the `TITLE:` line out for that meeting every time, so a title is
  asked for in one short question more.
- llama.cpp aborts the process if it ends with a model open and a request
  running; `_release_at_exit` stops the request and closes the model first.

**What is still wrong with it, plainly.** The 5,441-word meeting: 54 of 60
found in 84 to 96 s, and before the two rules six actions went to the wrong
person; after them the owner's own actions were right, and others were given
to "Speaker" or to nobody. Two small misreadings recurred on the 1,418-word
one ("goes down most weeks" read as a price that varies; a decision already
made listed as still to make). A key is better at long meetings, and the
Settings row says so. A dictation of more than about 750 words would outrun
the clean-up's deadline (arithmetic from its measured 26 words a second, not
tried) and be pasted as said.

**Which computers.** `local_llm.machine_verdict`: an Apple Silicon Mac with
16 GB or more is offered it; one with less is told why not (it would take
half of an 8 GB machine); Windows and Intel Macs are not shown it at all
(nothing was measured there, and those builds do not carry the engine).

**One request at a time.** A dictation goes ahead of notes, and notes already
being written stop at the next word and are written again afterwards
(measured: a dictation tidied in 7 s while a meeting's notes were under way,
notes 40 s in all instead of 27). After notes or a question the clean-up's
prompt is read again in the background. The model is released after five
idle minutes (`WAFFLER_LOCAL_LLM_IDLE_S`).

### On Windows

Windows has no llama-cpp-python build Waffler can ship (PyPI builds it from
source, which needs a compiler), so until November 2026 "On this computer" on
Windows pasted text as it was said: no tidy-up, no paragraphs. Now:

- **The engine** is llama.cpp's own `llama-server.exe`
  (`src/local_llm_server.py`): one model file, bound to 127.0.0.1 at a free
  port, `--fit on` so llama.cpp puts as many layers on the card as its free
  memory takes (1 GB spare), the OpenAI-style chat endpoint read word by word,
  a reply stopped by closing the connection, the reply read as UTF-8 (the
  stream says no charset; requests read it as Latin-1 and "£" came back as
  "Â£"). Which device it used is read from its log: a card only when a model
  buffer sits on one (`CUDA0 model buffer size`); the processor build also
  logs "offloaded 43/43 layers to GPU", meaning its own repacked copy.
- **It comes down with the model** (`src/llama_pack.py`), pinned like the
  models, into `<data folder>/llama`: with an NVIDIA card, llama.cpp b11168's
  CUDA build (255 MB) and NVIDIA's CUDA runtime wheel (3.6 MB; cuBLAS comes
  with the graphics card files, `src/gpu_pack.py`); otherwise its processor
  build (18.6 MB). One Download button, one size: the model plus the engine.
  Removing the model removes the engine.
- **Offered** (`machine_verdict`): an NVIDIA card of 4 GB or more
  (`"nvidia"`), or 16 GB of memory and 8 logical cores on the processor
  (`"cpu"`, slower, and the row says so: "Optional. Slower here, but free and
  private.").

Measured on the maintainer's PC (Windows 11, RTX 4070 SUPER, i7-14700F, 32 GB),
November 2026, through the app's own clean-up (`OpenAIStyler.style`) and
`write_notes`, with no key, and no connection beyond the computer after the
downloads:

| | On the card (43 of 43 layers, 3.1 GB) | On the processor |
|---|---|---|
| Load | 3.1 to 3.5 s | 2.4 to 3.9 s |
| First read of the clean-up's prompt (5,624 tokens) | about 1 s | about 40 s |
| Dictations of 34 to 69 words | 0.41 to 0.76 s | 3.1 to 6.0 s |
| A 200-word meeting's notes | 2.3 s | 16 s |

End to end as a new PC gets it: the processor build fetched from GitHub,
unpacked, and run; the CUDA build (the maintainer's own copy of the same zip, checked
against the pinned SHA-256) with the CUDA runtime fetched from PyPI, run on
the card with only the files Waffler fetched.

**After an idle unload** (five minutes) the clean-up's prompt is read back
from disk instead of read again. When the prompt has been read, the engine
keeps the slot (`--slot-save-path`, one file of about 113 MB in
`<data folder>/llama-cache`, named after the model and the prompt, so a
changed one is never read back). A newly loaded engine reads it back before
anything is asked. The model's sliding-window layers keep only 1,024 tokens,
so a kept slot could not be used at first: the server said "forcing full
prompt re-processing due to lack of cache data". On the processor
`--swa-full` keeps that cache whole (about 0.6 GB more memory). On a card it
is not used, because the prompt reads in about a second anyway. Measured
with the processor build: the first dictation after an unload took 46.5 s
and was pasted as said; now it takes 9.6 s in all and is tidied (85 tokens
read instead of 5,702). The next dictation takes 5.4 s.

Not done on Windows: nothing older than this PC was tried, and no laptop.

## An NVIDIA card on Windows

`src/gpu_pack.py`. faster-whisper uses an NVIDIA card when cuBLAS 12 and
cuDNN 9 load (`local_stt_ct2._cuda_usable`), and the packaged app ships
without them, so on a PC with a card "On this computer" ran on the processor.
Measured on the maintainer's PC (Windows 11, RTX 4070 SUPER, i7-14700F), November 2026:

| | Processor | Card |
|---|---|---|
| `small`, an 11.5 s synthetic clip, as the app decodes | 1.22 to 1.38 s | 0.22 to 0.36 s |
| Real dictations of 1.7 to 18.8 s (the speech step) | 2.4 to 4.5 s | 0.43 s (one 2.4 s dictation so far) |

His cloud dictations (Groq, last 294 in his normal Waffler's log) took 0.58 s
for the same step at the median (0.42 to 0.83 s for the middle half).

**What it does.** On one click in Settings, Keys and providers, under "On this
computer" (a row the page makes only where `gpu_pack_status()` says
`offered`: Windows, the faster-whisper engine, an NVIDIA card the driver
reports with at least 1.9 GB on it, and the libraries not loadable already),
it fetches NVIDIA's own Windows wheels from PyPI, pinned by version, size and
SHA-256 like the models (`nvidia-cublas-cu12` 12.9.2.10, 553 MB;
`nvidia-cudnn-cu12` 9.28.1.4, 696 MB), resumed if cut short, refused if the
disk is too full. It takes only the DLLs in each wheel's `bin` folder (not
`nvblas`) into `<data folder>/gpu`, writes a mark listing them, and deletes the
wheels: 12 files, 1.86 GB. Then the engine looks for the card again
(`local_stt_ct2.gpu_libraries_arrived`) and the machine is read again
(`local_stt.forget_machine`), so the next dictation uses the card with no
restart. Never on its own: 1.25 GB is the person's choice.

The real download through this module, on his connection: 1,249 MB in 42 s,
checked and unpacked in 54 s in all; a fresh process with only those files
then ran `small` on the card in 0.22 to 0.36 s.

**Removing.** Only what the mark lists is deleted (a `gpu` folder linked there
by hand is never written to or emptied). A DLL the engine has open cannot be
deleted on Windows; it is listed in `.remove` and deleted at the next start,
before anything loads it (`finish_pending_removal`, early in `main`).

**The warning.** Where speech runs on the processor the card's line says so
before anyone waits: "Slower on this computer, but free and private."
(`localCardLine`; "Slow and less accurate here, but free and private." on a
machine too small for `small`), and setup's free card says the same
(`setupLocalView` `warn`). Where it runs on the graphics (Apple Silicon, or the
card with its libraries) the line stays "Private. One download."

Tests: `tests/test_gpu_pack.py` (fake wheels and a fake server: fetched,
checked, unpacked, resumed, damaged, stopped, no space, removed with a file in
use; the page's lines within the few-words rules).

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

The writing model's engine (`llama-cpp-python`), added to `Waffler_mac.spec`.
Checked with a small frozen program only (PyInstaller 6.20, Python 3.14,
macOS arm64, unsigned): a throwaway spec made of the writing-model block
copied out of `Waffler_mac.spec`, around a script that only imports
`src/local_llm.py`. It collected the 21 library files into `llama_cpp/lib`,
found the downloaded model, answered on Metal and closed cleanly. The first
load in that new build took 16 s (macOS checks the libraries once per build),
against 2.5 s afterwards. The real spec was not built:

- It is not in `requirements.txt`, because PyPI has no binary wheel and every
  install would compile it. The release workflow must install it into the
  build's Python before PyInstaller runs, with the app's minimum macOS set
  (built without it here, the libraries came out needing macOS 26):
  `MACOSX_DEPLOYMENT_TARGET=14.0 CMAKE_ARGS="-DGGML_METAL=on
  -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0" pip install "llama-cpp-python==0.3.37"
  --no-binary llama-cpp-python` (about five minutes; CMake comes with the
  build, the compiler must be on the runner).
- `Waffler_mac.spec` then names `llama_cpp` (it is imported inside a function)
  and collects `llama_cpp/lib/*.dylib` into `llama_cpp/lib`
  (`collect_dynamic_libs`): the package opens `libllama`, `libggml-*` and
  `libmtmd` by path with ctypes, which PyInstaller cannot follow. With the
  package absent the spec builds as before and the app does not offer the
  download. pyinstaller-hooks-contrib 2026.4 has no hook for it.
- The installed package is 37 MB, 35 MB of it libraries, each present three
  times (the wheel turns `libllama.dylib -> libllama.1.dylib` links into
  copies). About 23 MB could be saved by collecting one name of each.
- Not in `Waffler_windows.spec` or `requirements_windows.txt`: not offered on
  Windows.
- To check on the first real build: Metal under the hardened runtime in the
  signed, notarised app, `scripts/check_macos_minos.py` (the probe's libraries
  were built for macOS 26: see the command above), and the first-use time of
  a signed build.

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
- `tests/test_local_llm.py` (the module, with the engine and network
  replaced), `tests/test_local_cleanup.py`, `tests/test_local_notes.py`,
  `tests/test_local_llm_page.py`, and the packaging tests above.
  `scripts/local_llm_check.py` is the real thing for the writing model: the
  5.2 GB download with a cancel and a resume, then, with the network switched
  off and no key, dictations tidied through `OpenAIStyler.style`, a meeting's
  notes through `write_notes`, a dictation during notes, the idle unload and
  removal. Not part of the suite.
- `scripts/local_stt_check.py`: the real thing. Downloads the model into a
  temporary data folder, cancels and resumes, transcribes two spoken clips,
  runs two callers at once, waits for the idle unload, removes the model.
  `--engine faster-whisper` runs the other engine. Not part of the suite.

## Not done

- The first-run screens were checked in a browser against a stand-in for
  the Python side, and the download and a dictation were run for real from a
  script; the screens were not driven in the real app window, on Windows, or
  on a machine the verdict calls small.
- The verdict's thresholds are the model-choice rule, not measurements: no
  Windows PC, Intel Mac or 8 GB Mac was timed.
- Any test on Windows, on an Intel Mac, with an NVIDIA card, or of a real
  release build.
- Accuracy on real speech. Only synthetic speech was measured.
- The writing model on anything but one M5 with 16 GB: no 8 GB Mac (it is
  not offered there), no other Apple Silicon chip, no Intel Mac, no Windows,
  no frozen build. Its speed on an M1 or M2 with 16 GB is not known; the
  verdict offers it there on memory alone.
- The writing model on real dictations and real meetings: the 28 dictations
  and three transcripts were written for the test, in English.
- The writing model's rows in Settings and at first run were not driven in
  the real app window; their logic is tested in Node and the markup as text.
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

  The Privacy page now says it for each way in use (`privacyText` in
  `ui/logic.js`): on this computer nothing you say leaves it; on your own key
  audio and text go straight to your provider; with Waffler Plus they go to
  the Waffler service, which keeps none of it (only numbers of use) and gets up
  to 200 words of the Vocabulary as a spelling hint on dictation. Transforms
  and Command mode follow Plus or the key, never the model on this computer.
- A dictation that arrives while a piece of a meeting is being transcribed
  waits for that piece (up to 28 s of audio). On the Mac's GPU that is about a
  second. On a slow Windows CPU it could be many seconds; the fix would be to
  let a dictation interrupt the piece and run it again afterwards.
- On a slow CPU a very long take can outrun the local speech deadline (twice
  the cloud one): at the speed of real time, a 20 minute take takes 20 minutes
  and the deadline is 20.5.
- The NVIDIA card's libraries are fetched by `src/gpu_pack.py` now (see "An
  NVIDIA card on Windows" above). Not done there: the first-run screens do not
  offer them (Settings does), and no card older than an RTX 4070 SUPER, no
  laptop card and no older driver was tried.
