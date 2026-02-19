"""
Subscription router
Manage Pro tier subscriptions (Stripe integration)
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database.config import get_db
from database.models import User, Subscription

router = APIRouter()


# Response models
class SubscriptionInfo(BaseModel):
    tier: str
    status: Optional[str]
    period_end: Optional[str]


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


# Endpoints
@router.get("/", response_model=SubscriptionInfo)
async def get_subscription(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    """
    Get current subscription information
    
    - Returns tier (free/pro)
    - Returns status if Pro
    - Returns period end date
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
    
    # Get subscription if exists
    subscription = db.query(Subscription).filter(
        Subscription.user_id == user.id
    ).first()
    
    return SubscriptionInfo(
        tier=user.tier,
        status=subscription.status if subscription else None,
        period_end=subscription.current_period_end.isoformat() if subscription and subscription.current_period_end else None
    )


@router.post("/upgrade")
async def upgrade_to_pro(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    """
    Upgrade to Pro tier
    
    - TODO: Integrate with Stripe
    - For now, just upgrades tier
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
    
    # Upgrade tier (TODO: integrate Stripe)
    user.tier = "pro"
    db.commit()
    
    return {
        "message": "Upgraded to Pro tier",
        "tier": "pro"
    }


@router.post("/cancel")
async def cancel_subscription(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    """
    Cancel Pro subscription
    
    - TODO: Integrate with Stripe
    - For now, just downgrades to free
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
    
    # Downgrade tier (TODO: integrate Stripe)
    user.tier = "free"
    db.commit()
    
    return {
        "message": "Subscription cancelled",
        "tier": "free"
    }
