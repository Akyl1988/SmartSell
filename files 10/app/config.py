import os

class BaseConfig:
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'sqlite:///smartsell.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'supersecret')
    TIPTOP_PUBLIC_ID = os.getenv('TIPTOP_PUBLIC_ID', '')
    TIPTOP_API_SECRET = os.getenv('TIPTOP_API_SECRET', '')
    WA_DAILY_LIMIT = int(os.getenv('DAILY_LIMIT', '800'))
    NIGHT_START = int(os.getenv('NIGHT_START', '22'))
    NIGHT_END = int(os.getenv('NIGHT_END', '8'))

class DevConfig(BaseConfig):
    DEBUG = True

class ProdConfig(BaseConfig):
    DEBUG = False

def get_config(name):
    if name == 'prod':
        return ProdConfig
    if name == 'dev':
        return DevConfig
    return BaseConfig