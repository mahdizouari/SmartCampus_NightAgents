def test_root_health_endpoint(client):
    """Test that GET /health returns 200 OK and conforms to HealthResponse schema."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app_name"] == "SmartCampus AI"
    assert "version" in data
    assert "environment" in data
    assert "timestamp" in data


def test_api_v1_health_endpoint(client):
    """Test that GET /api/v1/health returns 200 OK."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_root_endpoint(client):
    """Test that GET / returns 200 OK and basic API information."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "docs" in data
    assert "health" in data

