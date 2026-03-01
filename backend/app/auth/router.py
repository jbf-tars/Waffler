"""
Authentication router
Signup, signin, and token management endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import secrets
import jwt
from datetime import datetime, timedelta
from typing import Optional
import os

from database.config import get_db
from database.models import User

router = APIRouter()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24


# Request/Response models
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None


class SigninRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    user_id: str
    api_key: str
    tier: str
    email: str
    name: Optional[str]


# Helper functions
def hash_password(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def generate_api_key() -> str:
    """Generate a secure API key"""
    return f"vf_{secrets.token_urlsafe(32)}"


def create_access_token(data: dict) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# Endpoints
@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(request: SignupRequest, db: Session = Depends(get_db)):
    """
    Create a new user account
    
    - Returns API key for immediate use
    - Default tier: free (2,000 words/week)
    - Includes 7-day Pro trial
    """
    
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Hash password
    password_hash = hash_password(request.password)
    
    # Generate API key
    api_key = generate_api_key()
    
    # Create user
    new_user = User(
        email=request.email,
        name=request.name,
        password_hash=password_hash,
        tier="free",  # Start with free tier
        api_key=api_key
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return AuthResponse(
        user_id=str(new_user.id),
        api_key=new_user.api_key,
        tier=new_user.tier,
        email=new_user.email,
        name=new_user.name
    )


@router.post("/signin", response_model=AuthResponse)
async def signin(request: SigninRequest, db: Session = Depends(get_db)):
    """
    Sign in with email and password
    
    - Returns existing API key
    - Returns user tier information
    """
    
    # Find user
    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Verify password
    if not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    return AuthResponse(
        user_id=str(user.id),
        api_key=user.api_key,
        tier=user.tier,
        email=user.email,
        name=user.name
    )


@router.post("/refresh")
async def refresh_api_key(current_api_key: str, db: Session = Depends(get_db)):
    """
    Refresh API key (generate new one)
    
    - Requires current API key
    - Invalidates old key
    - Returns new key
    """
    
    # Find user by API key
    user = db.query(User).filter(User.api_key == current_api_key).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    
    # Generate new API key
    new_api_key = generate_api_key()
    user.api_key = new_api_key
    
    db.commit()
    db.refresh(user)
    
    return {"api_key": new_api_key}
