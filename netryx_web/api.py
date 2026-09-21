from __future__ import annotations

import hmac
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .listing import ListingFetcher
from .middleware import RequestBodyLimitMiddleware
from .photo_research import PhotoResearchService
from .research import build_research_service
from .security import UnsafeUrlError, validate_listing_url
from .store import JobStore, QueueFullError
from .uploads import UploadValidationError, cleanup_upload_dir, stage_uploads
from .worker import JobWorker


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


class GeolocationRequest(BaseModel):
    listing_url: str


def create_app(
    *,
    store: JobStore | None = None,
    research_service: Any | None = None,
    photo_research_service: Any | None = None,
    start_worker: bool = True,
) -> FastAPI:
    runtime_root = Path(os.environ.get("NETRYX_RUNTIME_DIR", ROOT / "runtime")).resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    actual_store = store or JobStore(runtime_root / "jobs.sqlite3")
    actual_service = research_service or build_research_service(
        results_root=os.environ.get("NETRYX_RESULTS_DIR", "/opt/data/cache/netryx"),
        work_root=runtime_root / "research",
        listing_fetcher=ListingFetcher(),
    )
    actual_photo_service = photo_research_service or PhotoResearchService(
        runtime_root,
        timeout_s=_positive_env_float("NETRYX_PHOTO_RESEARCH_TIMEOUT_S", 3_600),
    )
    max_active_jobs = _positive_env_int(
        "NETRYX_MAX_ACTIVE_JOBS",
        _positive_env_int("NETRYX_MAX_QUEUED_JOBS", 4),
    )
    auth_token = os.environ.get("NETRYX_API_TOKEN", "")
    worker = JobWorker(
        actual_store,
        actual_service,
        photo_research_service=actual_photo_service,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # A single worker owns all GPU work. Running rows cannot safely be
        # resumed after process loss, so make the interrupted state explicit.
        actual_store.recover_running_jobs()
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(
        title="Netryx Web API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    origins = [
        value.strip()
        for value in os.environ.get(
            "NETRYX_CORS_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000",
        ).split(",")
        if value.strip()
    ]
    app.add_middleware(RequestBodyLimitMiddleware)

    @app.middleware("http")
    async def api_authentication(request, call_next):
        if auth_token and (request.url.path == "/api" or request.url.path.startswith("/api/")) and request.method != "OPTIONS":
            header = request.headers.get("authorization", "")
            scheme, _, candidate = header.partition(" ")
            # compare_digest is used for every candidate, including malformed
            # headers; the scheme check only decides whether the result counts.
            matches = hmac.compare_digest(candidate.encode("utf-8"), auth_token.encode("utf-8"))
            if scheme.lower() != "bearer" or not matches:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "detail": {
                            "code": "AUTH_REQUIRED",
                            "message_fr": "Un jeton d’accès Bearer valide est requis.",
                        }
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    @app.middleware("http")
    async def no_store_api_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Wrap authentication too: browsers must be able to read 401 responses.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "auth_required": bool(auth_token)}

    @app.post("/api/v1/geolocations", status_code=status.HTTP_202_ACCEPTED)
    def create_geolocation(payload: GeolocationRequest, response: Response) -> dict[str, Any]:
        try:
            listing_url = validate_listing_url(payload.listing_url)
        except UnsafeUrlError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "INVALID_LISTING_URL", "message_fr": str(exc)},
            ) from exc
        try:
            job = actual_store.create(listing_url, max_active=max_active_jobs)
        except QueueFullError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "QUEUE_FULL",
                    "message_fr": "La file de recherche est momentanément pleine.",
                },
            ) from exc
        response.headers["Location"] = f"/api/v1/geolocations/{job['job_id']}"
        return _accepted_payload(job)

    @app.post("/api/v1/photo-geolocations", status_code=status.HTTP_202_ACCEPTED)
    def create_photo_geolocation(
        response: Response,
        photos: list[UploadFile] = File(...),
        latitude: str = Form(...),
        longitude: str = Form(...),
        radius_m: str = Form(...),
        reviewed_exterior: str = Form(...),
        image_size: str = Form("hd"),
    ) -> dict[str, Any]:
        latitude_value = _parse_float_field(
            latitude, code="INVALID_COORDINATES", message="Les coordonnées sont invalides."
        )
        longitude_value = _parse_float_field(
            longitude, code="INVALID_COORDINATES", message="Les coordonnées sont invalides."
        )
        if not -85 <= latitude_value <= 85 or not -180 <= longitude_value <= 180:
            raise _validation_error("INVALID_COORDINATES", "Les coordonnées sont invalides.")
        radius_value = _parse_float_field(
            radius_m,
            code="INVALID_RADIUS",
            message="Le rayon doit être compris entre 50 et 1000 mètres.",
        )
        if not 50 <= radius_value <= 1000:
            raise _validation_error(
                "INVALID_RADIUS", "Le rayon doit être compris entre 50 et 1000 mètres."
            )
        if reviewed_exterior.strip().lower() != "true":
            raise _validation_error(
                "EXTERIOR_REVIEW_REQUIRED",
                "La vérification visuelle de toutes les photos extérieures est obligatoire.",
            )
        image_size_value = image_size.strip().lower()
        if image_size_value not in {"sd", "hd"}:
            raise _validation_error(
                "INVALID_IMAGE_SIZE", "La définition d’image doit être sd ou hd."
            )

        upload_dir: Path | None = None
        try:
            upload_dir, images = stage_uploads(photos, runtime_root)
            payload = {
                "input_type": "photo",
                "latitude": latitude_value,
                "longitude": longitude_value,
                "radius_m": radius_value,
                "reviewed_exterior": True,
                "image_size": image_size_value,
                "images": images,
            }
            try:
                job = actual_store.create(
                    None,
                    input_type="photo",
                    payload=payload,
                    max_active=max_active_jobs,
                )
            except QueueFullError as exc:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "QUEUE_FULL",
                        "message_fr": "La file de recherche est momentanément pleine.",
                    },
                ) from exc
            response.headers["Location"] = f"/api/v1/geolocations/{job['job_id']}"
            # The queue row is committed only after stage_uploads has completed.
            upload_dir = None
            return _accepted_payload(job)
        except UploadValidationError as exc:
            raise _validation_error(exc.code, exc.message_fr) from exc
        finally:
            if upload_dir is not None:
                cleanup_upload_dir(upload_dir, runtime_root)

    @app.get("/api/v1/geolocations/{job_id}")
    def get_geolocation(job_id: str) -> dict[str, Any]:
        job = actual_store.get(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "JOB_NOT_FOUND",
                    "message_fr": "Cette analyse est inconnue ou a expiré.",
                },
            )
        return job

    @app.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(FRONTEND / "index.html")

    app.mount("/css", StaticFiles(directory=FRONTEND / "css"), name="frontend-css")
    app.mount("/js", StaticFiles(directory=FRONTEND / "js"), name="frontend-js")

    app.state.job_store = actual_store
    app.state.job_worker = worker
    app.state.runtime_root = runtime_root
    return app


def _accepted_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {"job_id": job["job_id"], "status": job["status"], "poll_after_ms": 800}


def _validation_error(code: str, message_fr: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message_fr": message_fr},
    )


def _parse_float_field(value: str, *, code: str, message: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise _validation_error(code, message) from exc
    if not math.isfinite(parsed):
        raise _validation_error(code, message)
    return parsed


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


app = create_app()
