"""
Database models
SQLAlchemy ORM models for users, usage, and subscriptions
"""

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Float, ForeignKey, TypeDecorator
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.sql import func
from database.config import Base
import uuid


# UUID type that works with both SQLite and PostgreSQL
class GUID(TypeDecorator):
    """Platform-independent GUID type - uses PostgreSQL UUID or String(36)"""
    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return str(value)
        else:
            if not isinstance(value, uuid.UUID):
                return str(uuid.UUID(value))
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(value)
        return value


class User(Base):
    """User account"""
    __tablename__ = "users"
    
    id = Column(GUID, primary_key=True, default=uuid.uuid4)
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
    
    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    words_used = Column(Integer, nullable=False)
    characters_used = Column(Integer, nullable=False)
    transcript = Column(String)  # Optional: privacy consideration
    success = Column(Boolean, default=True)


class Subscription(Base):
    """Subscription tracking"""
    __tablename__ = "subscriptions"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(GUID, ForeignKey("users.id"), unique=True, nullable=False)
    stripe_subscription_id = Column(String)
    status = Column(String)  # 'active' | 'cancelled' | 'past_due'
    current_period_start = Column(DateTime(timezone=True))
    current_period_end = Column(DateTime(timezone=True))
    canceled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class LLMUsage(Base):
    """Track serverless LLM usage for cost tracking"""
    __tablename__ = "llm_usage"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    cost = Column(Float, nullable=False)  # Actual cost in USD
    provider = Column(String, nullable=False)  # 'modal' | 'replicate' | 'local'
    model_name = Column(String)  # e.g., 'llama-3.1-70b', 'qwen-2.5-72b'
    success = Column(Boolean, default=True)
