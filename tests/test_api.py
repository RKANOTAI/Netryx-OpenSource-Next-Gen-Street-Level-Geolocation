from pathlib import Path

from fastapi.testclient import TestClient

from netryx_web.api import create_app
from netryx_web.store import JobStore


class UnusedResearchService:
    def run(self, listing_url, progress):  # pragma: no cover - worker disabled in this test
        raise AssertionError("worker should not run")


def test_submit_returns_202_and_pollable_job(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    app = create_app(
        store=store,
        research_service=UnusedResearchService(),
        start_worker=False,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/geolocations",
            json={"listing_url": "https://example.test/listing"},
        )

        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "queued"
        assert response.headers["location"] == f"/api/v1/geolocations/{payload['job_id']}"

        status = client.get(response.headers["location"])
        assert status.status_code == 200
        assert status.json()["status"] == "queued"


def test_health_endpoint():
    app = create_app(start_worker=False)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok", "auth_required": False}
