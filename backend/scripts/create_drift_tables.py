"""Script to create drift monitoring tables in the database."""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import engine, SessionLocal
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Date, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()


class DriftCheckLog(Base):
    """Logs drift monitoring checks for historical tracking."""
    __tablename__ = "drift_check_logs"
    
    id = Column(Integer, primary_key=True)
    check_date = Column(Date, nullable=False, index=True)
    status = Column(String(20), nullable=False)  # HEALTHY, WARNING, CRITICAL
    should_pause = Column(Boolean, default=False)
    hit_rate = Column(Float)
    edge_decay = Column(Float)
    report_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        # Unique constraint on check_date
        {'sqlite_autoincrement': True},
    )


class BetLog(Base):
    """Logs individual bets for performance tracking."""
    __tablename__ = "bet_logs"
    
    id = Column(Integer, primary_key=True)
    prediction_id = Column(Integer, ForeignKey("predictions.id"), nullable=False)
    stake_fraction = Column(Float, nullable=False)  # Fraction of bankroll
    odds = Column(Float, nullable=False)
    expected_value = Column(Float)
    bankroll_before = Column(Float, nullable=False)
    kelly_fraction = Column(Float, nullable=False)
    constraints_applied = Column(JSON)  # Which constraints were active
    result = Column(String(10))  # 'won', 'lost', 'pending'
    pnl = Column(Float)  # Profit/loss
    created_at = Column(DateTime, default=datetime.utcnow)


def create_tables():
    """Create the drift monitoring tables."""
    print("Creating drift monitoring tables...")
    
    try:
        # Create tables
        Base.metadata.create_all(engine)
        print("✓ DriftCheckLog table created")
        print("✓ BetLog table created")
        
        # Verify tables exist
        db = SessionLocal()
        from sqlalchemy import inspect
        
        inspector = inspect(db.bind)
        tables = inspector.get_table_names()
        
        if 'drift_check_logs' in tables:
            print("✓ drift_check_logs table verified")
        else:
            print("✗ drift_check_logs table not found")
        
        if 'bet_logs' in tables:
            print("✓ bet_logs table verified")
        else:
            print("✗ bet_logs table not found")
        
        db.close()
        print("\n✓ All drift monitoring tables created successfully!")
        return True
        
    except Exception as e:
        print(f"✗ Error creating tables: {e}")
        return False


if __name__ == "__main__":
    success = create_tables()
    sys.exit(0 if success else 1)