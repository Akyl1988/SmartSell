from fastapi import FastAPI

def create_app() -> FastAPI:
    app = FastAPI(title="SmartSell3 API")
    # TODO: подключи роуты, модели, middleware
    return app

app = create_app()