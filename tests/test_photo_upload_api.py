import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from netryx_web.api import create_app
from netryx_web.photo_research import PhotoResearchService
from netryx_web.store import JobStore, QueueFullError
from netryx_web.worker import JobWorker


class UnusedResearchService:
    def run(self, listing_url, progress):  # pragma: no cover - worker disabled in API tests
        raise AssertionError("listing worker should not run")


def _app(tmp_path, *, monkeypatch=None, token=None):
    if monkeypatch is not None:
        monkeypatch.setenv("NETRYX_RUNTIME_DIR", str(tmp_path / "runtime"))
        if token is None:
            monkeypatch.delenv("NETRYX_API_TOKEN", raising=False)
        else:
            monkeypatch.setenv("NETRYX_API_TOKEN", token)
    return create_app(
        store=JobStore(tmp_path / "jobs.sqlite3"),
        research_service=UnusedResearchService(),
        start_worker=False,
    )


def _photo_bytes(fmt="JPEG", *, size=(96, 80), exif=None):
    stream = io.BytesIO()
    image = Image.new("RGB", size, "navy")
    image.save(stream, format=fmt, exif=exif or b"")
    return stream.getvalue()


def _files(*payloads):
    return [
        ("photos", (f"photo-{index}.{fmt.lower()}", data, f"image/{fmt.lower()}"))
        for index, (fmt, data) in enumerate(payloads)
    ]


def test_photo_upload_is_queued_with_normalized_reviewed_manifest(tmp_path, monkeypatch):
    exif = Image.Exif()
    exif[34853] = {1: "N", 2: (48.0, 51.0, 0.0), 3: "E", 4: (2.0, 21.0, 0.0)}
    source = _photo_bytes("JPEG", exif=exif)
    app = _app(tmp_path, monkeypatch=monkeypatch)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/photo-geolocations",
            data={
                "latitude": "48.8566",
                "longitude": "2.3522",
                "radius_m": "250",
                "reviewed_exterior": "true",
                "image_size": "sd",
            },
            files=_files(("JPEG", source)),
        )

        assert response.status_code == 202
        accepted = response.json()
        assert accepted["status"] == "queued"
        snapshot = client.get(f"/api/v1/geolocations/{accepted['job_id']}").json()

    assert snapshot["job_id"] == accepted["job_id"]
    assert "payload_json" not in snapshot
    assert "photo-uploads" not in json.dumps(snapshot)
    payload = app.state.job_store.get_payload(accepted["job_id"])
    assert payload["input_type"] == "photo"
    assert payload["image_size"] == "sd"
    assert payload["reviewed_exterior"] is True
    item = payload["images"][0]
    normalized = Path(item["path"])
    assert normalized.is_file()
    assert normalized.name != "photo-0.jpeg"
    assert len(normalized.read_bytes()) == normalized.stat().st_size
    assert hashlib.sha256(normalized.read_bytes()).hexdigest() == item["sha256"]
    with Image.open(normalized) as image:
        assert image.format == "JPEG"
        assert image.getexif() == {}
        assert image.size == (96, 80)


def test_photo_upload_requires_true_exterior_approval(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch=monkeypatch)
    with TestClient(app) as client:
        missing = client.post(
            "/api/v1/photo-geolocations",
            data={"latitude": "48", "longitude": "2", "radius_m": "100"},
            files=_files(("JPEG", _photo_bytes())),
        )
        false = client.post(
            "/api/v1/photo-geolocations",
            data={
                "latitude": "48",
                "longitude": "2",
                "radius_m": "100",
                "reviewed_exterior": "false",
            },
            files=_files(("JPEG", _photo_bytes())),
        )

    assert missing.status_code == 422
    assert false.status_code == 422
    assert false.json()["detail"]["code"] == "EXTERIOR_REVIEW_REQUIRED"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("latitude", "86", "INVALID_COORDINATES"),
        ("longitude", "181", "INVALID_COORDINATES"),
        ("radius_m", "49", "INVALID_RADIUS"),
        ("radius_m", "1001", "INVALID_RADIUS"),
        ("image_size", "uhd", "INVALID_IMAGE_SIZE"),
    ],
)
def test_photo_upload_rejects_invalid_search_parameters(tmp_path, monkeypatch, field, value, code):
    app = _app(tmp_path, monkeypatch=monkeypatch)
    data = {
        "latitude": "48",
        "longitude": "2",
        "radius_m": "100",
        "reviewed_exterior": "true",
        "image_size": "hd",
        field: value,
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/photo-geolocations",
            data=data,
            files=_files(("JPEG", _photo_bytes())),
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code


def test_photo_upload_rejects_corrupt_small_and_unsupported_images(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch=monkeypatch)
    cases = [(b"not an image", "INVALID_IMAGE"), (_photo_bytes(size=(32, 80)), "IMAGE_DIMENSIONS_TOO_SMALL")]
    with TestClient(app) as client:
        for data, code in cases:
            response = client.post(
                "/api/v1/photo-geolocations",
                data={"latitude": "48", "longitude": "2", "radius_m": "100", "reviewed_exterior": "true"},
                files=_files(("JPEG", data)),
            )
            assert response.status_code == 422
            assert response.json()["detail"]["code"] == code
        response = client.post(
            "/api/v1/photo-geolocations",
            data={"latitude": "48", "longitude": "2", "radius_m": "100", "reviewed_exterior": "true"},
            files=[("photos", ("photo.gif", _photo_bytes("GIF"), "image/gif"))],
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "UNSUPPORTED_IMAGE_FORMAT"


def test_api_token_protects_every_api_route_but_not_health_or_options(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch=monkeypatch, token="correct-token")
    with TestClient(app) as client:
        health = client.get("/healthz")
        unauthorized = client.get("/api/v1/geolocations/nope")
        wrong = client.get("/api/v1/geolocations/nope", headers={"Authorization": "Bearer wrong"})
        authorized = client.get(
            "/api/v1/geolocations/nope", headers={"Authorization": "Bearer correct-token"}
        )
        preflight = client.options(
            "/api/v1/geolocations",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert health.json() == {"status": "ok", "auth_required": True}
    assert unauthorized.status_code == 401
    assert wrong.status_code == 401
    assert authorized.status_code == 404
    assert preflight.status_code == 200
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()


def test_store_migrates_payload_column_without_leaking_internal_paths(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE geolocation_jobs (
                job_id TEXT PRIMARY KEY, listing_url TEXT NOT NULL, status TEXT NOT NULL,
                phase TEXT NOT NULL, percent INTEGER, message_fr TEXT NOT NULL,
                result_json TEXT, error_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
    store = JobStore(path)
    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(geolocation_jobs)")}
    assert "payload_json" in columns
    job = store.create(
        None,
        input_type="photo",
        payload={"input_type": "photo", "manifest_path": "/srv/private/manifest.json"},
    )
    snapshot = store.get(job["job_id"])
    assert "/srv/private" not in json.dumps(snapshot)
    assert store.get_payload(job["job_id"])["manifest_path"] == "/srv/private/manifest.json"


def test_photo_worker_dispatches_injected_pipeline_and_releases_only_its_uploads(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    upload = tmp_path / "photo-uploads" / "owned"
    upload.mkdir(parents=True)
    marker = upload / "photo.jpg"
    marker.write_bytes(b"owned")
    job = store.create(
        None,
        input_type="photo",
        payload={"input_type": "photo", "images": [{"path": str(marker)}]},
    )
    calls = []

    class PhotoPipeline:
        def run(self, job_id, payload, progress):
            calls.append((job_id, payload["input_type"]))
            progress("matching", 50, "Comparaison…")
            return {"ok": True}

    worker = JobWorker(store, object(), photo_research_service=PhotoPipeline())
    worker._run(store.claim_next())

    assert calls == [(job["job_id"], "photo")]
    assert store.get(job["job_id"])["status"] == "succeeded"
    assert marker.is_file()


def test_photo_research_passes_size_per_subprocess_without_mutating_parent_env(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    photo = runtime / "photo-uploads" / "owned" / "photo.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"normalized")
    captured = {}

    class Completed:
        returncode = 0
        stdout = json.dumps({
            "location": {"latitude": 48.1, "longitude": 2.1},
            "confidence": {"level": "MEDIUM", "score": None},
            "evidence": [],
            "google_maps_url": "",
        })
        stderr = ""

    def runner(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return Completed()

    monkeypatch.delenv("NETRYX_PANORAMAX_IMAGE_SIZE", raising=False)
    service = PhotoResearchService(tmp_path / "runtime", runner=runner, timeout_s=9)
    result = service.run(
        "geo_test",
        {
            "input_type": "photo",
            "latitude": 48.0,
            "longitude": 2.0,
            "radius_m": 100,
            "image_size": "sd",
            "reviewed_exterior": True,
            "images": [{"path": str(photo), "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()}],
        },
        lambda *_: None,
    )

    assert result["location"]["latitude"] == 48.1
    assert captured["env"]["NETRYX_PANORAMAX_IMAGE_SIZE"] == "sd"
    assert "NETRYX_PANORAMAX_IMAGE_SIZE" not in __import__("os").environ
    manifest = Path(captured["command"][-1])
    assert manifest.is_file()
    assert json.loads(manifest.read_text())["center"] == {"latitude": 48.0, "longitude": 2.0}
    assert captured["cwd"] == str(manifest.parent)


def test_queue_limit_is_atomic_and_explicit(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    for index in range(4):
        store.create(f"https://example.test/{index}", max_active=4)
    with pytest.raises(QueueFullError):
        store.create("https://example.test/fifth", max_active=4)


def test_lifespan_recovers_stale_running_jobs(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create("https://example.test/stale")
    store.claim_next()
    app = create_app(store=store, research_service=UnusedResearchService(), start_worker=False)
    with TestClient(app):
        pass
    recovered = store.get(job["job_id"])
    assert recovered["status"] == "failed"
    assert recovered["error"]["code"] == "WORKER_RESTARTED"
    assert recovered["error"]["retryable"] is True
