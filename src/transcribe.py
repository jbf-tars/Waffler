"""Deepgram transcription module"""

# Support both deepgram-sdk v2 (Deepgram) and v3+ (DeepgramClient)
try:
    from deepgram import Deepgram  # legacy v2
    _DEEPGRAM_V2 = True
except ImportError:
    _DEEPGRAM_V2 = False
    Deepgram = None
    try:
        from deepgram import DeepgramClient  # v3+
    except ImportError:
        DeepgramClient = None

from typing import Optional
import time
import asyncio


class DeepgramTranscriber:
    """Transcribes audio using Deepgram API"""
    
    def __init__(self, api_key: str, model: str = "nova-2", language: str = "en-US"):
        self.api_key = api_key
        self.model = model
        self.language = language
        if _DEEPGRAM_V2 and Deepgram is not None:
            self.client = Deepgram(api_key)
        elif not _DEEPGRAM_V2:
            # deepgram-sdk v3+ — lazy init, transcribe_sync handles it
            self.client = None
        else:
            raise ImportError("deepgram-sdk not installed or incompatible version")
        
    def transcribe_sync(self, audio_bytes: bytes) -> str:
        """
        Transcribe audio bytes to text (synchronous)
        
        Args:
            audio_bytes: WAV format audio bytes
            
        Returns:
            Transcribed text
        """
        start_time = time.time()
        
        try:
            print(f"📡 Sending to Deepgram (model={self.model})...")
            
            # Use asyncio to run the async function
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Configure transcription options
            options = {
                "model": self.model,
                "language": self.language,
                "punctuate": True,
                "smart_format": True,
            }
            
            # Make API call
            source = {"buffer": audio_bytes, "mimetype": "audio/wav"}
            response = loop.run_until_complete(
                self.client.transcription.prerecorded(source, options)
            )
            
            # Extract transcript from response
            transcript = response["results"]["channels"][0]["alternatives"][0]["transcript"]
            
            latency = (time.time() - start_time) * 1000
            print(f"✅ Deepgram transcription complete ({latency:.0f}ms)")
            print(f"📝 Transcript: {transcript[:100]}..." if len(transcript) > 100 else f"📝 Transcript: {transcript}")
            
            loop.close()
            return transcript
            
        except Exception as e:
            print(f"❌ Deepgram error: {e}")
            import traceback
            traceback.print_exc()
            raise
