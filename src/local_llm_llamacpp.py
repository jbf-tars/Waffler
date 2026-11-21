"""The llama.cpp engine for src/local_llm.py.

``llama-cpp-python`` carries llama.cpp (built with Metal on Apple Silicon), so
the model runs on the Mac's GPU. Nothing is imported until a model is opened.
One model file (GGUF), opened by its path; the chat format is the one written
into the file by whoever made the model.

Every reply is read word by word as it is written, so a request can be
stopped at the next word: by its deadline, or by a dictation that is waiting
behind a meeting's notes.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

# Room for one part of a meeting (36,000 characters, about 10,000 tokens: see
# src/meetings/llm.py _PART_CHARS) plus the notes written from it, and for the
# clean-up's long prompt plus a long dictation.
N_CTX = 16384


class ContextTooLong(Exception):
    """What was asked does not fit the model's window."""


class LlamaCppEngine:
    def __init__(self, model_file: Path):
        from llama_cpp import Llama
        self.device = "metal" if (sys.platform == "darwin" and platform.machine() == "arm64") else "cpu"
        # flash_attn: measured a quarter quicker on an M5 and a little smaller.
        # The seed is fixed so the same words give the same answer.
        self._llm = Llama(str(model_file), n_gpu_layers=-1, n_ctx=N_CTX, flash_attn=True,
                          verbose=False, seed=7)

    def chat(self, system: str, messages: list, max_tokens: int, temperature: float,
             should_stop=None, on_text=None) -> dict:
        """One reply: ``{"text", "input_tokens", "output_tokens",
        "finish_reason"}``. ``should_stop()`` is asked after every word; when
        it says yes the reply ends there with finish_reason ``"stopped"``.
        ``on_text(words so far)`` is called as the reply is written.

        A prompt that starts like the one before it is not read again
        (llama.cpp keeps what it has read), which is what makes the clean-up
        quick: its long list of rules is the same for every dictation."""
        text, pieces, finish = "", 0, "stop"
        try:
            chunks = self._llm.create_chat_completion(
                messages=[{"role": "system", "content": system}, *messages],
                max_tokens=max_tokens, temperature=temperature, stream=True)
            try:
                for chunk in chunks:
                    choice = (chunk.get("choices") or [{}])[0]
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        text += piece
                        pieces += 1
                        if on_text is not None:
                            on_text(text)
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    if should_stop is not None and should_stop():
                        finish = "stopped"
                        break
            finally:
                close = getattr(chunks, "close", None)
                if close is not None:
                    close()             # ends the generation where it stands
        except ValueError as e:
            if "context window" in str(e):
                raise ContextTooLong(str(e)) from e
            raise
        read = max(0, int(getattr(self._llm, "n_tokens", 0) or 0) - pieces)
        return {"text": text.strip(), "input_tokens": read, "output_tokens": pieces,
                "finish_reason": finish}

    def close(self) -> None:
        llm, self._llm = self._llm, None
        close = getattr(llm, "close", None)
        if close is not None:
            close()
