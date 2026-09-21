from fastapi.testclient import TestClient

from netryx_web.api import create_app
from netryx_web.store import JobStore


ORIGIN = "https://rkanotai.github.io"


def test_auth_errors_are_readable_by_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("NETRYX_API_TOKEN", "test-only-token")
    monkeypatch.setenv("NETRYX_CORS_ORIGINS", ORIGIN)
    app = create_app(store=JobStore(tmp_path / "jobs.sqlite3"), start_worker=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/geolocations/unknown", headers={"Origin": ORIGIN})
        assert response.status_code == 401
        assert response.headers.get("access-control-allow-origin") == ORIGIN
        assert response.headers["cache-control"] == "no-store"
        assert client.options("/api/v1/photo-geolocations", headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization",
        }).status_code == 200


def test_non_ascii_token_is_rejected_not_server_error(tmp_path, monkeypatch):
    monkeypatch.setenv("NETRYX_API_TOKEN", "test-only-token")
    app = create_app(store=JobStore(tmp_path / "jobs.sqlite3"), start_worker=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/geolocations/unknown", headers={b"Authorization": b"Bearer \xff"})
        assert response.status_code == 401


def test_oversized_body_is_rejected_before_multipart_parsing(tmp_path, monkeypatch):
    monkeypatch.delenv("NETRYX_API_TOKEN", raising=False)
    app = create_app(store=JobStore(tmp_path / "jobs.sqlite3"), start_worker=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/photo-geolocations", content=b"x", headers={
            "Content-Length": str(43 * 1024 * 1024),
            "Content-Type": "multipart/form-data; boundary=x",
        })
        assert response.status_code == 413
