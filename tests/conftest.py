import pytest
from app.main import create_app
from app.extensions import db
@pytest.fixture
def app():
    test_app = create_app()
    test_app.config['TESTING'] = True
    test_app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///:memory:"
    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()

@pytest.fixture
def client(app):
    return app.test_client()
