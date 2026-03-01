"""
Transcription Router
Proxy endpoint for OpenAI Whisper transcription
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, status
from pydantic import BaseModel
import os
import tempfile
import openai

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


class TranscribeResponse(BaseModel):
    text: str


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Transcribe audio using OpenAI Whisper API.

    Accepts multipart audio file, returns transcribed text.
    Auth is handled by the app-secret middleware.
    """

    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Transcription service not configured. Add OPENAI_API_KEY to backend .env"
        )

    # Read uploaded audio
    audio_bytes = await file.read()

    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty audio file"
        )

    # Write to temp file (OpenAI SDK needs a file-like object with a name)
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        with open(tmp_path, "rb") as audio_file:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )

        return TranscribeResponse(text=result.text)

    except openai.AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid OpenAI API key configured on server"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transcription failed: {str(e)}"
        )
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
