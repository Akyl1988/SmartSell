#!/usr/bin/env python3
"""Reset test database"""
import os
import psycopg2

ADMIN_URL = os.getenv("ADMIN_DATABASE_URL") or os.getenv("DATABASE_URL") or "postgresql://postgres@localhost:5432/postgres"

conn = psycopg2.connect(ADMIN_URL)
conn.autocommit = True
cur = conn.cursor()

try:
    cur.execute('DROP DATABASE IF EXISTS "SmartSellTest"')
    print("Dropped SmartSellTest database")
except Exception as e:
    print(f"Drop failed: {e}")

try:
    cur.execute('CREATE DATABASE "SmartSellTest"')
    print("Created SmartSellTest database")
except Exception as e:
    print(f"Create failed: {e}")

cur.close()
conn.close()
