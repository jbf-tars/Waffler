"""
LLM Styling Router
Endpoint for cleaning up transcribed text using Replicate's serverless LLMs
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import os
import replicate

from database.config import get_db
from database.models import User, LLMUsage

router = APIRouter()

# Replicate API token from environment
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")


class StyleRequest(BaseModel):
    transcript: str
    api_key: Optional[str] = None
    prompt_style: Optional[str] = "smart"
    vocabulary: Optional[List[str]] = []


class StyleResponse(BaseModel):
    styled_text: str
    usage: dict
    cost: float


def verify_api_key(api_key: str, db: Session) -> User:
    """Verify user's API key and return user"""
    user = db.query(User).filter(User.api_key == api_key).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return user


def get_quota_for_tier(tier: str) -> int:
    """Get monthly transcription quota based on user tier"""
    quotas = {
        "free": 20,
        "plus": 100,
        "pro": 500,
        "unlimited": 999999
    }
    return quotas.get(tier, 20)


def get_monthly_usage_count(user_id: str, db: Session) -> int:
    """Count user's LLM usage this month"""
    from datetime import datetime, timedelta
    from sqlalchemy import func, extract

    # Get current month/year
    now = datetime.utcnow()

    count = db.query(func.count(LLMUsage.id)).filter(
        LLMUsage.user_id == user_id,
        extract('year', LLMUsage.timestamp) == now.year,
        extract('month', LLMUsage.timestamp) == now.month,
        LLMUsage.success == True
    ).scalar()

    return count or 0


def load_prompt_template(style: str) -> str:
    """Load prompt template based on style"""
    # For now, use a default template
    # Later, load from prompts/ directory like the desktop app
    templates = {
        "smart": """You are a voice-to-text assistant. Clean up this rambling speech transcript and rewrite it as clear, structured text.

Rules:
- Remove filler words (um, uh, like, you know, so, basically)
- Fix backtracking and repetition
- Preserve ALL ideas and technical details
- Detect content type and format appropriately:
  * Lists → bullet points
  * Messages → professional prose
  * Commands → actionable directives
  * Notes → structured bullets
  * Code → preserve code blocks
- Output ONLY the cleaned text, nothing else

Transcript: {transcript}""",

        "normal": """Clean up this transcript. Remove filler words (um, uh, like) and make it readable. Output only the cleaned text.

Transcript: {transcript}""",

        "adhd_ramble": """You are a voice-to-text assistant for ADHD brains. Clean up this rambling speech transcript and rewrite it as clear, structured text. Remove filler words (um, uh, like, you know), fix backtracking, preserve all ideas and technical details. Output ONLY the cleaned text, nothing else.

Transcript: {transcript}"""
    }

    return templates.get(style, templates["smart"])


@router.post("/style", response_model=StyleResponse)
async def style_transcript(
    request: StyleRequest,
    db: Session = Depends(get_db)
):
    """
    Style/clean up transcribed text using Replicate's LLM

    - Requires valid API key
    - Checks user quota based on tier
    - Uses Llama 3.1 70B for text cleanup
    - Logs usage and cost to database
    """

    # Check if Replicate token is configured
    if not REPLICATE_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service not configured. Add REPLICATE_API_TOKEN to backend/.env"
        )

    # Verify user and check quota (if api_key provided)
    # When called via X-App-Secret middleware, api_key may be omitted
    user = None
    if request.api_key:
        user = verify_api_key(request.api_key, db)
        monthly_usage = get_monthly_usage_count(str(user.id), db)
        quota = get_quota_for_tier(user.tier)
        if monthly_usage >= quota:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Monthly quota exceeded. Used {monthly_usage}/{quota} transcriptions."
            )

    # Load prompt template
    prompt_template = load_prompt_template(request.prompt_style)

    # Add vocabulary hints if provided
    vocab_hint = ""
    if request.vocabulary:
        vocab_hint = f"\n\nPreserve these exact spellings: {', '.join(request.vocabulary)}"

    prompt = prompt_template.format(transcript=request.transcript) + vocab_hint

    try:
        # Call Replicate API with Llama 3 70B
        # Using Meta's Llama 3 70B Instruct model (162M runs, verified)
        output = replicate.run(
            "meta/meta-llama-3-70b-instruct",
            input={
                "top_p": 0.9,
                "prompt": prompt,
                "max_tokens": 512,
                "temperature": 0.3,
                "system_prompt": "You are a voice-to-text formatter. Output ONLY the final cleaned text. No meta-commentary, labels, or explanations."
            }
        )

        # Join output (Replicate streams tokens)
        styled_text = "".join(output).strip()

        # Estimate token usage (rough approximation)
        # Replicate doesn't return exact token counts
        input_tokens = len(prompt.split()) * 1.3  # Rough estimate
        output_tokens = len(styled_text.split()) * 1.3

        # Calculate cost
        # Llama 3.1 70B on Replicate: ~$0.001 per 1000 input tokens, ~$0.003 per 1000 output tokens
        input_cost = (input_tokens / 1000) * 0.001
        output_cost = (output_tokens / 1000) * 0.003
        total_cost = input_cost + output_cost

        # Log usage to database (if user identified)
        if user:
            llm_usage = LLMUsage(
                user_id=user.id,
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                cost=total_cost,
                provider="replicate",
                model_name="meta-llama-3-70b-instruct",
                success=True
            )
            db.add(llm_usage)
            db.commit()

        return StyleResponse(
            styled_text=styled_text,
            usage={
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
            },
            cost=total_cost
        )

    except Exception as e:
        # Log failed attempt (if user identified)
        if user:
            llm_usage = LLMUsage(
                user_id=user.id,
                input_tokens=0,
                output_tokens=0,
                cost=0.0,
                provider="replicate",
                model_name="meta-llama-3-70b-instruct",
                success=False
            )
            db.add(llm_usage)
            db.commit()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM styling failed: {str(e)}"
        )


@router.get("/quota")
async def check_quota(
    api_key: str,
    db: Session = Depends(get_db)
):
    """Check user's quota status"""
    user = verify_api_key(api_key, db)

    monthly_usage = get_monthly_usage_count(str(user.id), db)
    quota = get_quota_for_tier(user.tier)

    return {
        "tier": user.tier,
        "quota": quota,
        "used": monthly_usage,
        "remaining": quota - monthly_usage
    }
