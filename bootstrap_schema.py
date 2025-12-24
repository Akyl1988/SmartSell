#!/usr/bin/env python3
"""
Bootstrap test database schema manually using SQLAlchemy models.
This bypasses Alembic's issues with circular FKs.
"""

import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.models.base import Base, ensure_models_loaded

async def bootstrap_schema():
    """Create all tables using SQLAlchemy models."""
    
    print("📦 Loading and registering all models...")
    ensure_models_loaded()
    
    # Async URL for metadata operations
    async_url = "postgresql+asyncpg://smartsell:admin123@127.0.0.1:5432/smartsell_test"
    
    print(f"\n📦 Bootstrapping schema for smartsell_test...")
    print(f"   Tables registered: {len(Base.metadata.tables)}")
    print(f"   Using engine: {async_url}")
    
    # Create async engine
    engine = create_async_engine(async_url, echo=False)
    
    try:
        # Create all tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ All tables created successfully")
        
        # Verify tables exist
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"))
            table_count = result.fetchone()[0]
            print(f"✅ Verified: {table_count} tables in public schema")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(bootstrap_schema())
