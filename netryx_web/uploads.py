from __future__ import annotations

import hashlib
import io
import os
import shutil
import uuid
import warnings
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps


MAX_PHOTOS = 4
MAX_BYTES_PER_PHOTO = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 42 * 1024 * 1024
MIN_DIMENSION = 64
MAX_PIXELS = 40_000_000
SUPPORTED_FORMATS = {"JPEG": ".jpg", "PNG": ".png"}


class UploadValidationError(ValueError):
    def __init__(self, code: str, message_fr: str):
        super().__init__(message_fr)
        self.code = code
        self.message_fr = message_fr


def stage_uploads(uploads: Iterable[Any], runtime_root: str | Path) -> tuple[Path, list[dict[str, Any]]]:
    """Decode, normalize, and atomically stage browser photos below runtime_root."""
    items = list(uploads)
    if not 1 <= len(items) <= MAX_PHOTOS:
        raise UploadValidationError(
            "INVALID_PHOTO_COUNT",
            "Il faut fournir entre 1 et 4 photos.",
        )

    root = Path(runtime_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "photo-uploads" / uuid.uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    total_bytes = 0
    records: list[dict[str, Any]] = []
    try:
        for index, upload in enumerate(items):
            raw = _read_upload(upload)
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_BYTES:
                raise UploadValidationError(
                    "PHOTO_TOTAL_TOO_LARGE",
                    "La taille totale des photos dépasse 42 Mio.",
                )
            records.append(_normalize_one(raw, destination, index))
        return destination, records
    except Exception:
        cleanup_upload_dir(destination, root)
        raise


def cleanup_upload_dir(directory: str | Path, runtime_root: str | Path) -> None:
    """Remove only one upload directory when it is owned by runtime_root."""
    root = Path(runtime_root).resolve()
    candidate = Path(directory).resolve()
    try:
        candidate.relative_to(root / "photo-uploads")
    except ValueError:
        return
    if candidate != root / "photo-uploads":
        shutil.rmtree(candidate, ignore_errors=True)


def _read_upload(upload: Any) -> bytes:
    stream = getattr(upload, "file", upload)
    try:
        raw = stream.read(MAX_BYTES_PER_PHOTO + 1)
    except OSError as exc:
        raise UploadValidationError(
            "PHOTO_READ_FAILED", "Une photo n’a pas pu être lue."
        ) from exc
    if not isinstance(raw, bytes):
        raise UploadValidationError("INVALID_IMAGE", "Le contenu envoyé n’est pas une image.")
    if len(raw) == 0:
        raise UploadValidationError("INVALID_IMAGE", "Une photo est vide ou illisible.")
    if len(raw) > MAX_BYTES_PER_PHOTO:
        raise UploadValidationError(
            "PHOTO_TOO_LARGE",
            "Chaque photo doit faire au maximum 10 Mio.",
        )
    return raw


def _normalize_one(raw: bytes, destination: Path, index: int) -> dict[str, Any]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                image_format = str(source.format or "").upper()
                if image_format not in SUPPORTED_FORMATS:
                    raise UploadValidationError(
                        "UNSUPPORTED_IMAGE_FORMAT",
                        "Seuls les formats JPEG et PNG sont acceptés.",
                    )
                width, height = source.size
                if width < MIN_DIMENSION or height < MIN_DIMENSION:
                    raise UploadValidationError(
                        "IMAGE_DIMENSIONS_TOO_SMALL",
                        "Chaque photo doit mesurer au moins 64 × 64 pixels.",
                    )
                if width * height > MAX_PIXELS:
                    raise UploadValidationError(
                        "IMAGE_DIMENSIONS_TOO_LARGE",
                        "La résolution de la photo est trop élevée.",
                    )
                source.load()
                normalized = ImageOps.exif_transpose(source)
                try:
                    if image_format == "JPEG":
                        normalized = normalized.convert("RGB")
                        output_format = "JPEG"
                    elif "A" in normalized.getbands():
                        normalized = normalized.convert("RGBA")
                        output_format = "PNG"
                    else:
                        normalized = normalized.convert("RGB")
                        output_format = "PNG"
                    output = destination / f"{uuid.uuid4().hex}{SUPPORTED_FORMATS[image_format]}"
                    temporary = destination / f".{uuid.uuid4().hex}.part"
                    try:
                        save_kwargs = {"format": output_format, "exif": b""}
                        if output_format == "JPEG":
                            save_kwargs.update({"quality": 95, "optimize": True})
                        else:
                            save_kwargs.update({"optimize": True})
                        normalized.save(temporary, **save_kwargs)
                        with temporary.open("rb") as stream:
                            os.fsync(stream.fileno())
                        os.replace(temporary, output)
                    finally:
                        temporary.unlink(missing_ok=True)
                finally:
                    if normalized is not source:
                        normalized.close()
    except UploadValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise UploadValidationError(
            "IMAGE_DIMENSIONS_TOO_LARGE",
            "La résolution de la photo est trop élevée.",
        )
    except (OSError, SyntaxError, ValueError) as exc:
        raise UploadValidationError(
            "INVALID_IMAGE", "Une photo est corrompue ou illisible."
        ) from exc

    digest = _sha256(output)
    return {
        "path": str(output),
        "sha256": digest,
        "format": image_format,
        "width": width,
        "height": height,
        "index": index,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
