#!/usr/bin/env python3
"""Reset test database"""
import psycopg2

conn = psycopg2.connect("postgresql://postgres:admin123@localhost:5432/postgres")
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
