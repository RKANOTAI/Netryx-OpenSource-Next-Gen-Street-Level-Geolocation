from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .research import ResearchError, _validate_result
from .uploads import cleanup_upload_dir


class PhotoResearchService:
    """Dispatch one browser-photo manifest to the existing live runner.

    The subprocess boundary keeps model/GPU work out of the API event loop. The
    runner environment is copied per invocation so one job cannot mutate the
    process-wide image-size setting for another job.
    """

    def __init__(
        self,
        runtime_root: str | Path,
        *,
        runner_script: str | Path | None = None,
        timeout_s: float = 3_600,
        runner: Callable[..., Any] = subprocess.run,
    ):
        self.runtime_root = Path(runtime_root).resolve()
        self.upload_root = (self.runtime_root / "photo-uploads").resolve()
        self.research_root = self.runtime_root / "photo-research"
        self.runner_script = Path(runner_script or Path(__file__).with_name("live_runner.py")).resolve()
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        self.timeout_s = timeout_s
        self.runner = runner

    def run(
        self,
        job_id: str,
        payload: dict[str, Any],
        progress: Callable[[str, int | None, str], None],
    ) -> dict[str, Any]:
        images = self._validate_images(payload)
        latitude, longitude = self._coordinates(payload)
        radius_m = self._radius(payload)
        image_size = payload.get("image_size", "hd")
        if image_size not in {"sd", "hd"}:
            raise ResearchError(
                "INVALID_IMAGE_SIZE",
                "La définition d’image demandée est invalide.",
            )
        if payload.get("reviewed_exterior") is not True:
            raise ResearchError(
                "EXTERIOR_REVIEW_REQUIRED",
                "La vérification visuelle de toutes les photos extérieures est obligatoire.",
            )

        safe_job_id = str(job_id)
        if not safe_job_id or Path(safe_job_id).name != safe_job_id or safe_job_id in {".", ".."}:
            raise ResearchError("INVALID_JOB_ID", "L’identifiant de recherche est invalide.")
        work_dir = (self.research_root / safe_job_id).resolve()
        try:
            work_dir.relative_to(self.research_root.resolve())
        except ValueError as exc:  # pragma: no cover - generated job IDs are safe
            raise ResearchError("INVALID_JOB_ID", "L’identifiant de recherche est invalide.") from exc
        work_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = work_dir / "listing-manifest.json"
        manifest = {
            "acquisition": "browser_photos",
            "images": images,
            "center": {"latitude": latitude, "longitude": longitude},
            "search_radius_m": radius_m,
            "exterior_review": {
                "approved_sha256": [item["sha256"] for item in images],
                "reviewer": "browser operator",
            },
        }
        temporary = work_dir / ".listing-manifest.json.part"
        try:
            progress("preparing_photo_manifest", 10, "Préparation des photos vérifiées…")
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, manifest_path)
            progress("matching", 20, "Recherche d’images de rue autour de la zone indiquée…")
            environment = os.environ.copy()
            environment["NETRYX_PANORAMAX_IMAGE_SIZE"] = image_size
            # Keep an explicit generic value for deployments that consume the
            # contract name, while live_runner uses the Panoramax-specific one.
            environment["NETRYX_IMAGE_SIZE"] = image_size
            try:
                completed = self.runner(
                    [sys.executable, str(self.runner_script), str(manifest_path)],
                    cwd=str(work_dir),
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ResearchError(
                    "PHOTO_RESEARCH_TIMEOUT",
                    "La recherche photo a dépassé la durée maximale autorisée.",
                    retryable=True,
                ) from exc
            if getattr(completed, "returncode", 1) != 0:
                raise ResearchError(
                    "PHOTO_RESEARCH_FAILED",
                    "Le moteur Netryx n’a pas pu terminer la recherche photo.",
                    retryable=True,
                )
            try:
                result = json.loads(getattr(completed, "stdout", ""))
                if not isinstance(result, dict):
                    raise TypeError("result must be an object")
                _validate_result(result)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError, IndexError) as exc:
                raise ResearchError(
                    "INVALID_RESEARCH_OUTPUT",
                    "Le moteur Netryx a renvoyé un résultat invalide.",
                    retryable=False,
                ) from exc
            progress("complete", 100, "Analyse photo terminée.")
            return result
        finally:
            temporary.unlink(missing_ok=True)
            self._cleanup_inputs(images)

    def _validate_images(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        raw_images = payload.get("images")
        if not isinstance(raw_images, list) or not 1 <= len(raw_images) <= 4:
            raise ResearchError("INVALID_PHOTO_COUNT", "Il faut fournir entre 1 et 4 photos.")
        images: list[dict[str, str]] = []
        seen_paths: set[Path] = set()
        seen_hashes: set[str] = set()
        upload_directory: Path | None = None
        for raw in raw_images:
            if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
                raise ResearchError("INVALID_IMAGE", "Le manifeste photo est invalide.")
            path = Path(raw["path"]).resolve()
            try:
                path.relative_to(self.upload_root)
            except ValueError as exc:
                raise ResearchError("INVALID_IMAGE_PATH", "Le manifeste photo pointe hors du répertoire contrôlé.") from exc
            if upload_directory is None:
                upload_directory = path.parent
            elif path.parent != upload_directory:
                raise ResearchError(
                    "INVALID_IMAGE_PATH",
                    "Les photos d’une tâche doivent provenir du même répertoire contrôlé.",
                )
            if path in seen_paths or not path.is_file():
                raise ResearchError("INVALID_IMAGE", "Une photo du manifeste est absente ou dupliquée.")
            seen_paths.add(path)
            actual_hash = _sha256(path)
            claimed_hash = raw.get("sha256")
            if not isinstance(claimed_hash, str) or actual_hash != claimed_hash:
                raise ResearchError(
                    "PHOTO_INPUT_CHANGED",
                    "Une photo a changé depuis sa validation.",
                )
            if actual_hash in seen_hashes:
                raise ResearchError("DUPLICATE_PHOTO", "Deux photos ont le même contenu.")
            seen_hashes.add(actual_hash)
            images.append({"path": str(path), "sha256": actual_hash})
        return images

    @staticmethod
    def _coordinates(payload: dict[str, Any]) -> tuple[float, float]:
        try:
            latitude = float(payload["latitude"])
            longitude = float(payload["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchError("INVALID_COORDINATES", "Les coordonnées sont invalides.") from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -85 <= latitude <= 85 or not -180 <= longitude <= 180:
            raise ResearchError("INVALID_COORDINATES", "Les coordonnées sont invalides.")
        return latitude, longitude

    @staticmethod
    def _radius(payload: dict[str, Any]) -> float:
        try:
            radius = float(payload["radius_m"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchError("INVALID_RADIUS", "Le rayon doit être compris entre 50 et 1000 mètres.") from exc
        if not math.isfinite(radius) or not 50 <= radius <= 1000:
            raise ResearchError("INVALID_RADIUS", "Le rayon doit être compris entre 50 et 1000 mètres.")
        return radius

    def _cleanup_inputs(self, images: list[dict[str, str]]) -> None:
        if not images:
            return
        cleanup_upload_dir(Path(images[0]["path"]).resolve().parent, self.runtime_root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
