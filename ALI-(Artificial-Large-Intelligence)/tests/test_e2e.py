from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

def test_status_endpoint():
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json()["status"] == "online"

def test_chat_endpoint():
    response = client.post("/chat", json={"message": "Hello ALI, this is a test.", "session_id": "test_123"})
    assert response.status_code == 200

def test_memory_endpoint():
    response = client.get("/memory")
    assert response.status_code == 200
    assert "knowledge_base_entries" in response.json()

from unittest.mock import patch
import os

@patch.dict(os.environ, {"ALI_API_TOKEN": "test_token"})
def test_ruflow_endpoint_unauthorized():
    response = client.post("/ruflow")
    assert response.status_code == 401

if __name__ == "__main__":
    print("Running E2E tests...")
    test_status_endpoint()
    test_memory_endpoint()
    test_ruflow_endpoint_unauthorized()
    test_chat_endpoint()
    print("Tests completed.")
