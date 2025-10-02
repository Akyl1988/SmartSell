"""Tests for app.main module."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app, create_app


class TestCreateApp:
    """Test create_app function."""

    def test_create_app_returns_fastapi_instance(self):
        """Test that create_app returns a FastAPI instance."""
        test_app = create_app()
        assert isinstance(test_app, FastAPI)

    def test_app_has_correct_title(self):
        """Test that app has correct title."""
        test_app = create_app()
        assert test_app.title == "SmartSell"

    def test_app_has_correct_version(self):
        """Test that app has correct version."""
        test_app = create_app()
        assert test_app.version == "0.1.0"


class TestAppEndpoints:
    """Test application endpoints."""

    def test_root_endpoint(self):
        """Test root endpoint."""
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "SmartSell" in data["message"]

    def test_health_check_endpoint(self):
        """Test health check endpoint."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["version"] == "0.1.0"


class TestAppInstance:
    """Test global app instance."""

    def test_app_instance_exists(self):
        """Test that app instance exists."""
        assert app is not None
        assert isinstance(app, FastAPI)

    def test_app_instance_title(self):
        """Test app instance title."""
        assert app.title == "SmartSell"
