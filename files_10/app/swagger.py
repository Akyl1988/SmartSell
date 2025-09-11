from flasgger import Swagger

def setup_swagger(app):
    """
    Initialize Swagger (Flasgger) for SmartSell2.
    """
    template = {
        "swagger": "2.0",
        "info": {
            "title": "SmartSell2 API",
            "version": "1.0.0",
        },
        "basePath": "/",
        "schemes": ["http"],
    }
    Swagger(app, template=template)