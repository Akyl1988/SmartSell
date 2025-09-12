# Re-export create_app and app from main.py
from app.main import create_app, app

__all__ = ['create_app', 'app']