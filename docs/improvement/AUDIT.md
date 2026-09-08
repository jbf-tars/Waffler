# Waffler maintainer audit

Branch `audit/maintainer-2026-09` (worktree `C:\Users\james\waffler-audit`),
from `1b671a3` (v3.14.85). Started 2026-09-08.

Evidence rules used throughout: a claim is **PROVEN** only if reproduced here;
otherwise it is **LIKELY** (consistent with evidence, alternatives not excluded)
or **UNPROVEN**. Prior sessions' success claims are treated as leads, not proof.

---

## F1 — Installed build is NOT the released build  (PROVEN; root cause UNPROVEN)

| | |
|---|---|
| Source `src/__init__.py` | 3.14.85 |
| Installed `…\Programs\Waffler\_internal\src\__init__.py` | **3.14.84**, mtime 2026-07-15 15:34 |
| Latest `app.log` startup | v3.14.84 |

The v3.14.85 in-app update ran and **passed every verification stage**:

```
11:09:46 [updater] requests download done: 33206541 bytes
11:09:49 [updater] SHA-256 digest verified … 568e9b6f818af6e5…
11:09:49 [updater] Authenticode status: 'NotSigned' … (advisory)
        <no further update log line ever>
12:53:04 === Waffler starting === (v3.14.84)
```

So the app exited into the install batch and returned **1h43m later on the old
version**, with no error in the log and nothing shown to the user.

Ruled out by evidence:
* **Not** the pre-v3.14.82 Authenticode hard-fail — the digest gate passed and
  Authenticode is correctly advisory.
* **Not** `%TEMP%` cleanup destroying artifacts — `%TEMP%` retains files back to
  2025-06-18 and **367 files from Jul–Aug 2026 survive**.
* **Not** a Defender quarantine that Defender recorded — `Get-MpThreatDetection`
  returns no detections, none mentioning Waffler (real-time protection is on, so
  an unrecorded block is not excluded).
* **Not** an exception in `install_and_restart` — that path logs
  `install failed: …`, which appears for the 3.14.83 attempts but not this one.

Unexplained: the downloaded `waffler-update-*-Waffler-Setup-3.14.85.exe` and the
generated `waffler_update_<pid>.bat` are both **absent**, and Inno's
`%TEMP%\waffler_install.log` was **never created** — i.e. the installer appears
never to have run, yet the batch is gone.

**The defect that is proven regardless of root cause:** the update path has *no
post-install verification and no failure surface*. `_install_windows` ignores the
installer's exit code, unconditionally relaunches and self-deletes, and nothing
on the next start compares the running version against the version the user
asked to install. **A failed update is indistinguishable from a successful one.**
That fully explains the user's repeated "I updated and it's still the same
version" reports. Fix proposed as A1.

**Consequence for this audit:** the user is running 3.14.84, so none of the
v3.14.85 work (incomplete-transcript retry, 18 MB chunk gate, size-scaled upload
timeouts) was active for the September incident.

---

## F2 — The ASR filter deleted the user's own words  (PROVEN, FIXED `a3723b3`)

`_strip_hallucinations` matched stock outros anchored only to end-of-string,
with no grammatical guard and no reference to audio. Reproduced offline against
v3.14.85:

| input | output before |
|---|---|
| `Please send the deck to Priya and thank you.` | `…to Priya and` |
| `I'll sign it. Over to you.` | `…Over to` |
| `…onboarding, payroll, benefits and more.` | `…benefits` |
| `The tutorial ends by saying thanks for watching.` | `…by saying` |
| `Thank you.` | `` (empty) |

Silent, unrecoverable loss. The dangling function word (`Over to`) is the
structural signature of a phrase that was *integrated speech*, not an appended
outro. Fixed with three guards — sentence boundary, no-dangling-word, and
measured audio evidence — with 29 negative/positive control tests.

**Also proven: this filter does NOT produce the September "and the rest."
ending** (`test_and_the_rest_is_not_touched`). That loss is upstream of
filtering. See F3.

---

## F3 — September 3 truncation: narrowed, not solved  (PARTIALLY PROVEN)

History `2026-09-03T19:23:44` ends `"…Correct me if I'm wrong. and the rest."`
in **both** `text` (74 w) and `styled` (68 w); the tails are byte-identical.

* **PROVEN:** the ending predates styling — the styler did not truncate it.
* **PROVEN:** the ASR hallucination filter does not strip `and the rest`, so
  filtering did not remove the missing speech either.
* **UNPROVEN:** whether the loss occurred in capture or in ASR. Distinguishing
  these **requires the audio**, which was not retained for that recording.
* The lower-case `and` after a full stop is consistent with an ASR segment
  boundary artifact, but that is a hypothesis, not a finding.

**Blocker:** no audio for 2026-09-03. v3.14.85 added local retention of the last
10 recordings, but it is not installed (F1), so retention is not running. This
is the single highest-leverage unblock: F1 → retention active → next incident is
adjudicable.

---

## F4 — "Original" was a filtered artifact  (PROVEN, FIXED `3095c56`)

`transcribe_sync` returned filtered text; `app.py` saved it as history `text`;
the UI offered it as **"Show original"**. The provider's actual response was
stored nowhere, so any filter error was permanently unrecoverable — which is
precisely why F3 cannot be resolved from history.

Fixed: untouched response preserved (`asr_text`), field meaning stamped
(`text_is`), legacy entries documented as filtered-by-older-filter rather than
relabelled, and the UI button corrected to "Show transcript".

---

## Open backlog (not yet addressed; ranked)

| # | Item | Why it matters | Evidence status |
|---|---|---|---|
| ~~A1~~ | ~~Updater cannot detect its own failure~~ | **FIXED** `89c7301` | Defect proven |
| A2 | `_retry_if_incomplete` heuristics unvalidated | Words/sec + 1.25× rules can miss omissions and can prefer a longer *hallucinated* transcript | UNPROVEN either way; needs the fixture set |
| A3 | Styling guard misses partial loss | `finish_reason=length` + <50% word rule cannot catch one dropped crucial clause | Structural, unproven in the wild |
| A4 | ASR vs styling provider routing conflated | One `provider_order` drives both; Cerebras is silently skipped for ASR | Confirmed by code read |
| ~~A5~~ | ~~Groq model 404 handling~~ | **FIXED** `4fc2085`. Re-verified: 458 occurrences, latest 2026-09-08 08:14:15; 404 fell through to a bare `raise` with no cooldown | Proven |
| A6 | No versioned evaluation fixture set | Every fidelity claim so far rests on synthetic or single examples | See EVALUATION.md |


---

## F5 — The checks could not fail  (PROVEN, FIXED `d915f28`)

* CI's pyflakes step ended in `|| echo "no lint findings"` → **always exit 0**.
  It had been reporting `app.py: undefined name 'subprocess'`: `download_logs()`
  calls `subprocess.Popen` but imports the module only inside *other* methods,
  so both calls raised `NameError`, swallowed by a bare `except`. The log zip
  was written but the folder never opened and nothing said why. Fixed; guard
  verified by negative control (removing the import again is caught).
* `pytest tests/` imported two live benchmark scripts at collection time, which
  `load_dotenv` the user's real keys and read their private `history.json`
  while contributing **zero** test functions. The "offline" suite therefore
  depended on the maintainer's credentials and would error for any contributor.
  Excluded via `tests/conftest.py`; isolation proven by running with a
  redirected home and cleared keys.
* `test_and_more_hallucination.py::main()` ran a **hardcoded list** that had
  drifted: it named a renamed function (NameError under CI's
  `python tests/<file>.py`, invisible to pytest) and omitted a newly added test.
  Replaced with discovery.
* CI invoked test files individually → files added later were never run. Now
  runs the full suite.
* CI ran only on `macos-14`, skipping every Windows-only module, on a product
  that ships a Windows build. Added a `windows-latest` job.

## F6 — Unavailable model re-probed every dictation  (PROVEN, FIXED `4fc2085`)

458 Groq `404 model_not_found` for `llama-3.3-70b-versatile` (latest
2026-09-08 08:14:15) with the user on groq-first. The except-chain cooled down
429s, connection errors and 401/403 but let 404 reach a bare `raise`, so the
model was re-probed on every recording. Now a 1-hour cooldown plus a distinct
`MODEL_UNAVAILABLE` error naming the model. **Not** silently swapped for
another model: the message is ambiguous between "retired" and "this key lacks
access", and a default change needs a benchmark plus a docs check.
