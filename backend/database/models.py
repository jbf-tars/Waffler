"""
Database models
SQLAlchemy ORM models for users, usage, and subscriptions
"""

from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from database.config import Base
import uuid


class User(Base):
    """User account"""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String)
    password_hash = Column(String, nullable=False)
    tier = Column(String, default="free")  # 'free' | 'pro'
    stripe_customer_id = Column(String)
    api_key = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class UsageLog(Base):
    """Usage tracking log"""
    __tablename__ = "usage_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    words_used = Column(Integer, nullable=False)
    characters_used = Column(Integer, nullable=False)
    transcript = Column(String)  # Optional: privacy consideration
    success = Column(Boolean, default=True)


class Subscription(Base):
    """Subscription tracking"""
    __tablename__ = "subscriptions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False)
    stripe_subscription_id = Column(String)
    status = Column(String)  # 'active' | 'cancelled' | 'past_due'
    current_period_start = Column(DateTime(timezone=True))
    current_period_end = Column(DateTime(timezone=True))
    canceled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
