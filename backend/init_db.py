"""
Initialize database
Creates all tables from SQLAlchemy models
"""

from database.config import engine, Base
from database.models import User, UsageLog, Subscription

def init_database():
    """Create all database tables"""
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created successfully!")


if __name__ == "__main__":
    init_database()
