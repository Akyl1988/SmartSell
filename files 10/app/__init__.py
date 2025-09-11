from app.swagger import setup_swagger

def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))
    
    # Логирование и Swagger
    setup_logging(app)
    setup_swagger(app)
    # ... остальной код