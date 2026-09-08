# Evaluation record

## What has actually been run

| Layer | Status | Detail |
|---|---|---|
| Offline unit/integration | **Run** | `python -m pytest tests/ -q` → 134 passed, 1 skipped, on `3095c56`, Python 3.13, Windows. No API keys, no private history, no microphone. |
| Live ASR evaluation | **Not run** | Not authorised (no spend/call budget). Would also consume the user's Groq free-tier quota. |
| Live formatting evaluation | **Not run** | Same. `scripts/auto_test_corpus.py` and `scripts/flaky_check.py` exist and are the right harnesses when authorised. |
| Real-device / manual | **Not run** | Requires the user's mic and a built RC. |

## What the offline tests do and do not establish

They establish **deterministic filter and formatter behaviour** on specified
inputs: given this text, the function preserves or removes exactly this. That is
the right tool for F2/F4, which are logic defects.

They establish **nothing** about speech-recognition accuracy. There are no real
recordings in the suite. Synthetic tones and mocked provider responses cannot
measure omission rates, and a passing suite is not evidence that dictation is
faithful.

## Fixture set — specified, not yet built (backlog A6)

Blocked on the user approving a fixture set and (for the live layers) a budget.
Design constraints carried from the brief:

* **Versioned and non-sensitive.** No private recordings; expected content
  written down independently of any model output.
* **Coverage:** short/long; quiet/slow/fast; pauses; background noise; speech
  starting late and ending abruptly; chunk boundaries; numbers, names,
  negations; self-corrections; topic changes; and legitimate endings —
  "thank you", bare "you", "thanks for watching", "and the rest".
* **Metrics reported separately:** omissions and unsupported additions
  (a longer output is not a better one); final-clause retention;
  names/numbers/negation retention; WER where a reference exists; latency
  p50/p95; cost; retry rate; fallback rate; genuinely-empty-speech rate.
* **Held-out subset** excluded from any prompt tuning.
* **Recorded per run:** sample count, repeats, failures, model IDs, *actual*
  provider used (pinned, so fallback cannot count as that provider passing),
  prompt version/hash, source commit, parameters, fixture version.

## Statistical honesty

Zero failures in a small suite does not bound the production failure rate. Under
independent representative trials, ~300 clean trials are needed for a ≈95% upper
bound near 1%. Repeated calls on one easy input establish nothing. No percentage
reliability claim will be made from a handful of examples, and quota will not be
burned to manufacture one.

## Provider pinning note

`_normalize_provider_order` re-appends missing providers so the app's fallback
chain cannot be emptied. Any evaluation that claims to test one provider must
override the normalised order *after* construction, or a "Groq-pinned" run can
silently be answered by Cerebras. Both harnesses were previously corrected for
this; re-verify before trusting any per-provider number.
