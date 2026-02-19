"""
Usage tracking router
Log and retrieve usage statistics
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional

from database.config import get_db
from database.models import User, UsageLog

router = APIRouter()

# Constants
FREE_TIER_WEEKLY_LIMIT = 2000  # words per week


# Request/Response models
class LogUsageRequest(BaseModel):
    words_used: int
    characters_used: int
    transcript: Optional[str] = None


class UsageResponse(BaseModel):
    words_used: int
    words_limit: int
    reset_date: datetime
    percentage_used: float


# Helper functions
def get_user_from_api_key(api_key: str, db: Session) -> User:
    """Get user from API key or raise 401"""
    user = db.query(User).filter(User.api_key == api_key).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    return user


def get_weekly_usage(user_id: str, db: Session) -> int:
    """Get total words used in the current week"""
    # Calculate start of current week (Monday)
    today = datetime.utcnow().date()
    start_of_week = today - timedelta(days=today.weekday())
    start_datetime = datetime.combine(start_of_week, datetime.min.time())
    
    # Sum words used since start of week
    total = db.query(func.sum(UsageLog.words_used)).filter(
        UsageLog.user_id == user_id,
        UsageLog.timestamp >= start_datetime,
        UsageLog.success == True
    ).scalar()
    
    return total or 0


def get_reset_date() -> datetime:
    """Get the date when weekly quota resets (next Monday)"""
    today = datetime.utcnow().date()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    next_monday = today + timedelta(days=days_until_monday)
    return datetime.combine(next_monday, datetime.min.time())


# Endpoints
@router.post("/log", response_model=UsageResponse)
async def log_usage(
    request: LogUsageRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    """
    Log usage for a transcription
    
    - Requires: Authorization header with API key
    - Checks quota for free tier
    - Returns updated usage stats
    """
    
    # Extract API key from header
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header"
        )
    api_key = authorization.replace("Bearer ", "")
    
    # Get user
    user = get_user_from_api_key(api_key, db)
    
    # Check quota (only for free tier)
    if user.tier == "free":
        weekly_usage = get_weekly_usage(str(user.id), db)
        
        # Check if adding this would exceed quota
        if weekly_usage + request.words_used > FREE_TIER_WEEKLY_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Weekly quota exceeded. Upgrade to Pro for unlimited usage."
            )
    
    # Log usage
    usage_log = UsageLog(
        user_id=user.id,
        words_used=request.words_used,
        characters_used=request.characters_used,
        transcript=request.transcript,
        success=True
    )
    
    db.add(usage_log)
    db.commit()
    
    # Get updated usage
    weekly_usage = get_weekly_usage(str(user.id), db)
    words_limit = FREE_TIER_WEEKLY_LIMIT if user.tier == "free" else -1  # -1 = unlimited
    
    return UsageResponse(
        words_used=weekly_usage,
        words_limit=words_limit,
        reset_date=get_reset_date(),
        percentage_used=(weekly_usage / words_limit * 100) if words_limit > 0 else 0
    )


@router.get("/quota", response_model=UsageResponse)
async def get_quota(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    """
    Get current usage quota
    
    - Returns words used this week
    - Returns quota limit
    - Returns reset date
    """
    
    # Extract API key
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header"
        )
    api_key = authorization.replace("Bearer ", "")
    
    # Get user
    user = get_user_from_api_key(api_key, db)
    
    # Get usage
    weekly_usage = get_weekly_usage(str(user.id), db)
    words_limit = FREE_TIER_WEEKLY_LIMIT if user.tier == "free" else -1
    
    return UsageResponse(
        words_used=weekly_usage,
        words_limit=words_limit,
        reset_date=get_reset_date(),
        percentage_used=(weekly_usage / words_limit * 100) if words_limit > 0 else 0
    )
