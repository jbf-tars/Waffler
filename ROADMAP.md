# Waffler Roadmap

Deliberately deferred work — the "known, planned, not done yet" list. This is
**not** a changelog; see `CHANGELOG.md` for what has actually shipped.

## Reliability

### Transcription fallback when a VPN blocks Groq  *(planned)*

**Problem.** Transcription (speech → text) runs on **Groq Whisper**. Some VPN
exit IPs are blocked by Groq at the network layer — an `HTTP 403` returned
*before* authentication. When that happens and no alternative transcription
engine is configured (the hosted build ships Groq + Cerebras but an empty
`OPENAI_API_KEY`, and Cerebras is an LLM that can't transcribe), speech-to-text
itself fails. There is no "raw text" to fall back to because no text was ever
produced.

This is exit-IP specific: two people on the same VPN provider get different
exit IPs, so it can break for one user and work fine for another.

**Shipped mitigation (v3.14.54).** We no longer lose the recording. On a
transcription failure the raw audio is written to
`~/.waffler-hosted/unsent/recording-<timestamp>.wav`, a journal entry is added
so it's visible in History, and the user gets a clear toast telling them to
turn the VPN off and retry. The proper *automatic* fix is still outstanding:

**Planned fix (one or both):**

- **OpenAI Whisper fallback** — bake a working OpenAI key into the hosted build
  so transcription transparently falls back to OpenAI Whisper when Groq is
  blocked. The code path already exists in `src/transcribe_whisper.py`
  (`transcribe_sync` → `_transcribe_api`); it just needs a non-empty
  `OPENAI_API_KEY`. Small per-use cost, incurred only on the rare fallback.
- **On-device Whisper fallback** — bundle `faster-whisper` so transcription can
  run locally with no network at all. Zero per-use cost and works offline;
  costs ~100 MB+ of app size and a slower first run while the model loads.

**Stretch:** auto-reprocess the saved `unsent/` recordings once a working
transcription provider is reachable again, so the user doesn't have to
re-record at all.

**Workarounds available today (no rebuild):**

- **Switch VPN server/location.** The block is per-IP — different servers sit
  on different exit IPs, and only some ranges are on Groq's block list.
  Hopping servers usually lands on a clean one. (Confirmed empirically: same
  VPN provider, flips between working and blocked as you change location.)
- **Split-tunnel / exclude Waffler from the VPN.** Most VPN clients can exclude
  a specific app from the tunnel. Excluding Waffler routes its Groq requests
  over the real ISP IP (never blocked) while the rest of the machine stays on
  the VPN. One-time setup, then Groq always works.

Note: there is **no client-side way** to make Groq accept a blocked IP — the
`403 "Access denied. Please check your network settings."` is returned by
Groq's edge *before* authentication, so no key/header/request change affects
it. The only durable fixes are a non-Groq transcription path (above) or
bypassing the blocked IP (the workarounds).
