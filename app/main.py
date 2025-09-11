from flask import Flask
from app.config import get_config
from app.logging import setup_logging
from app.swagger import setup_swagger
from app.extensions import db, migrate, jwt


def create_app(config_name='default'):
    """Flask application factory function."""
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    
    # Setup logging and Swagger
    setup_logging(app)
    setup_swagger(app)
    
    # Register blueprints/routes here if needed
    # from app.routes import some_blueprint
    # app.register_blueprint(some_blueprint)
    
    return app


# Create app instance
app = create_app()