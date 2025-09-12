# app/swagger.py
from fastapi import FastAPI

def setup_swagger(app: FastAPI):
    """
    Настройки Swagger для FastAPI.
    В FastAPI Swagger включен по умолчанию по адресу /docs
    """
    app.title = "SmartSell3 API"
    app.version = "1.0.0"
    app.description = "API для SmartSell3"
    # Можно добавить contact, license, terms_of_service и т.д.
    return app
