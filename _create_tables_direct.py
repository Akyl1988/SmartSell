#!/usr/bin/env python3
"""Create tables using SQLAlchemy directly (bypasses Alembic)"""
import sys
from sqlalchemy import create_engine, MetaData

# Import models
from app.models import ensure_models_loaded, Base

# Load models
print("Loading models...")
ensure_models_loaded()

# Connect
DB_URL = "postgresql://postgres:admin123@localhost:5432/SmartSellTest"
print(f"Connecting to {DB_URL}...")
engine = create_engine(DB_URL)

# Create all tables - SQLAlchemy handles FK order automatically
print("Creating tables...")
try:
    Base.metadata.create_all(engine)
    print("✅ Tables created!")
    
    # List tables
    metadata = MetaData()
    metadata.reflect(bind=engine)
    tables = sorted(metadata.tables.keys())
    print(f"\nCreated {len(tables)} tables:")
    for i, name in enumerate(tables, 1):
        print(f"  {i:2}. {name}")
except Exception as e:
    print(f"❌ Error: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
