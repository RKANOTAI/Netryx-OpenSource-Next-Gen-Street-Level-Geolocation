"""Panoramax federation imagery provider.

The provider deliberately keeps discovery/download concerns separate from the
ML-heavy indexing path.  Discovery only talks to the public federation API;
model imports happen inside :func:`build_index`.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

import requests


SEARCH_URL = "https://api.panoramax.xyz/api/search"
DEFAULT_SEARCH_LIMIT = 1000
DEFAULT_MAX_IMAGES = 1000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
TRUSTED_ASSET_HOSTS = {
    "panoramax.ign.fr",
    "panoramax.openstreetmap.fr",
}
ALLOWED_LICENSES = {
    "etalab-2.0": "etalab-2.0",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
}


def _allowed_asset_hosts() -> set[str]:
    hosts = set(TRUSTED_ASSET_HOSTS)
    extra = os.environ.get("NETRYX_PANORAMAX_ALLOWED_HOSTS", "")
    hosts.update(item.strip().lower().rstrip(".") for item in extra.split(",") if item.strip())
    return hosts


def _trusted_https_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        # Accessing .port also rejects malformed port values.
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.hostname
        or parsed.hostname.lower().rstrip(".") not in _allowed_asset_hosts()
    ):
        return None
    return value.strip()


def _haversine_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    radius = 6_371_000.0
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(
        (lon2 - lon1) / 2
    ) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, a)))


def _bbox(center: tuple[float, float], radius_m: float) -> tuple[float, float, float, float]:
    latitude, longitude = center
    lat_delta = radius_m / 111_320.0
    cos_lat = max(abs(math.cos(math.radians(latitude))), 0.1)
    lon_delta = radius_m / (111_320.0 * cos_lat)
    return (
        longitude - lon_delta,
        latitude - lat_delta,
        longitude + lon_delta,
        latitude + lat_delta,
    )


def _write_audit(work_dir: Path, audit: dict[str, Any]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "pano-crawl.json"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=work_dir, prefix=".pano-crawl-", suffix=".tmp", delete=False
        ) as handle:
            json.dump(audit, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _max_images() -> int:
    raw = os.environ.get("NETRYX_PANORAMAX_MAX_IMAGES", str(DEFAULT_MAX_IMAGES))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("NETRYX_PANORAMAX_MAX_IMAGES must be an integer") from exc
    if value <= 0:
        raise ValueError("NETRYX_PANORAMAX_MAX_IMAGES must be positive")
    return value


def _search_limit() -> int:
    raw = os.environ.get(
        "NETRYX_PANORAMAX_SEARCH_LIMIT",
        os.environ.get("NETRYX_PANORAMAX_MAX_FEATURES", str(DEFAULT_SEARCH_LIMIT)),
    )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("NETRYX_PANORAMAX_SEARCH_LIMIT must be an integer") from exc
    if value <= 0:
        raise ValueError("NETRYX_PANORAMAX_SEARCH_LIMIT must be positive")
    # The federation endpoint documents 1000 as its maximum page size.
    return min(value, DEFAULT_SEARCH_LIMIT)


def _feature_license(properties: dict[str, Any]) -> str | None:
    value = properties.get("license")
    if not isinstance(value, str):
        return None
    return ALLOWED_LICENSES.get(value.strip().lower())


def _field_of_view(properties: dict[str, Any]) -> float | None:
    candidates: list[Any] = [
        properties.get("field_of_view"),
        properties.get("view:field_of_view"),
    ]
    interior = properties.get("pers:interior_orientation")
    if isinstance(interior, dict):
        candidates.append(interior.get("field_of_view"))
    for candidate in candidates:
        try:
            if candidate is not None:
                return float(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _number(properties: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        try:
            value = properties.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _provider_names(feature: dict[str, Any], properties: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("attribution", "providers", "geovisio:producer"):
        value = properties.get(key)
        if value is None:
            value = feature.get(key)
        if value is not None:
            values.append(value)

    names: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            candidate = value.strip()
        elif isinstance(value, dict):
            candidate = str(value.get("name") or value.get("title") or "").strip()
        else:
            candidate = ""
        if candidate and candidate not in names:
            names.append(candidate)

    for value in values:
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item)
        else:
            add(value)
    return names


def _asset_href(feature: dict[str, Any]) -> str | None:
    assets = feature.get("assets")
    if not isinstance(assets, dict):
        return None
    preferred = os.environ.get("NETRYX_PANORAMAX_IMAGE_SIZE", "sd").strip().lower()
    if preferred not in {"sd", "hd"}:
        raise ValueError("NETRYX_PANORAMAX_IMAGE_SIZE must be sd or hd")
    keys = (preferred, "hd" if preferred == "sd" else "sd")
    candidates = [assets[key].get("href") for key in keys if isinstance(assets.get(key), dict)]
    for candidate in candidates:
        url = _trusted_https_url(candidate)
        if url:
            return url
    return None


def _feature_link(feature: dict[str, Any], relation: str) -> str | None:
    links = feature.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, dict) and link.get("rel") == relation and isinstance(link.get("href"), str):
            return link["href"]
    return None


def _normalise_feature(feature: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(feature, dict):
        return None, "feature"
    raw_id = feature.get("id")
    try:
        panoid = str(uuid.UUID(str(raw_id)))
    except (ValueError, TypeError, AttributeError):
        return None, "id"

    geometry = feature.get("geometry")
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        return None, "geometry"
    try:
        lon, lat = float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError):
        return None, "geometry"
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, "geometry"

    properties = feature.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    license_name = _feature_license(properties)
    if license_name is None:
        return None, "license"
    href = _asset_href(feature)
    if href is None:
        return None, "asset"

    field_of_view = _field_of_view(properties)
    camera_azimuth = _number(properties, "view:azimuth", "view:heading", "camera_azimuth")
    if camera_azimuth is not None:
        camera_azimuth %= 360.0
    pitch = _number(properties, "view:pitch", "pers:pitch", "pitch")
    roll = _number(properties, "view:roll", "pers:roll", "roll")
    self_url = _feature_link(feature, "self")
    source_url = (
        properties.get("source_url")
        or properties.get("url")
        or feature.get("source_url")
        or self_url
        or href
    )
    license_url = properties.get("license_url") or _feature_link(feature, "license")
    datetime_value = (
        properties.get("datetime")
        or properties.get("capture:datetime")
        or properties.get("date")
    )
    perspective: bool | None
    if field_of_view is None:
        perspective = None
    else:
        perspective = not math.isclose(field_of_view, 360.0, abs_tol=0.01)

    return {
        "panoid": panoid,
        "lat": lat,
        "lon": lon,
        "provider": "panoramax",
        "source_url": str(source_url) if source_url is not None else href,
        "image_url": href,
        "image_variant": "hd" if href == feature.get("assets", {}).get("hd", {}).get("href") else "sd",
        "license": license_name,
        "license_url": license_url,
        "attribution": _provider_names(feature, properties),
        "datetime": datetime_value,
        "camera_azimuth": camera_azimuth,
        "pitch": pitch,
        "roll": roll,
        "field_of_view": field_of_view,
        "perspective": perspective,
        "heading_reference": "image_center" if perspective is False else "geographic",
    }, None


def discover(
    centers: Iterable[tuple[float, float]], radius_m: float, work_dir: str | Path
) -> list[dict[str, Any]]:
    """Discover licensed Panoramax images around every requested center.

    The federation endpoint is intentionally queried with a bounded bbox and
    then filtered with a great-circle distance.  A full page is not treated as
    exhaustive: a saturated response is an explicit failure requiring a
    narrower search.
    """
    work_path = Path(work_dir)
    center_list = [(float(lat), float(lon)) for lat, lon in centers]
    audit: dict[str, Any] = {
        "provider": "panoramax",
        "api_url": SEARCH_URL,
        "centers": center_list,
        "radius_m": float(radius_m),
        "limit": None,
        "queries": [],
        "panos": [],
        "returned_count": 0,
        "skipped_counts": {key: 0 for key in ("feature", "id", "geometry", "license", "asset", "outside_circle")},
        "coverage_status": "error",
    }
    try:
        if radius_m <= 0:
            raise ValueError("radius_m must be positive")
        if not center_list:
            audit["coverage_status"] = "empty"
            _write_audit(work_path, audit)
            raise RuntimeError("PANORAMAX_NO_CENTERS: no search centers were provided")
        if any(not (-90 <= lat <= 90 and -180 <= lon <= 180) for lat, lon in center_list):
            raise ValueError("center coordinates are outside valid latitude/longitude ranges")

        limit = _search_limit()
        audit["limit"] = limit
        unique: dict[str, dict[str, Any]] = {}
        saturated = False
        for center in center_list:
            west, south, east, north = _bbox(center, float(radius_m))
            bbox_value = f"{west:.8f},{south:.8f},{east:.8f},{north:.8f}"
            query_audit: dict[str, Any] = {
                "center": list(center),
                "bbox": bbox_value,
                "limit": limit,
                "features_count": 0,
                "coverage_saturated": False,
            }
            try:
                response = requests.get(
                    SEARCH_URL,
                    params={"bbox": bbox_value, "limit": limit},
                    headers={"User-Agent": "Netryx-Web/1.0"},
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                query_audit["error"] = f"{type(exc).__name__}: {exc}"
                audit["queries"].append(query_audit)
                audit["coverage_status"] = "error"
                _write_audit(work_path, audit)
                raise RuntimeError(f"PANORAMAX_API_ERROR: search request failed: {exc}") from exc

            features = payload.get("features") if isinstance(payload, dict) else None
            if not isinstance(features, list):
                features = []
            query_audit["features_count"] = len(features)
            query_audit["coverage_saturated"] = len(features) >= limit
            saturated = saturated or query_audit["coverage_saturated"]
            audit["queries"].append(query_audit)
            for feature in features:
                pano, reason = _normalise_feature(feature)
                if pano is None:
                    audit["skipped_counts"][reason or "feature"] += 1
                    continue
                if _haversine_m(center, (pano["lat"], pano["lon"])) > float(radius_m):
                    audit["skipped_counts"]["outside_circle"] += 1
                    continue
                unique.setdefault(pano["panoid"], pano)

        panos = list(unique.values())
        audit["panos"] = panos
        audit["returned_count"] = len(panos)
        if saturated:
            audit["coverage_status"] = "saturated"
            _write_audit(work_path, audit)
            raise RuntimeError(
                "PANORAMAX_COVERAGE_TRUNCATED: search response reached its feature limit; narrow radius"
            )
        if len(panos) > _max_images():
            audit["coverage_status"] = "limited"
            audit["max_images"] = _max_images()
            _write_audit(work_path, audit)
            raise RuntimeError(
                f"PANORAMAX_MAX_IMAGES_EXCEEDED: {len(panos)} images exceed the configured bound; narrow radius"
            )
        if not panos:
            audit["coverage_status"] = "empty"
            _write_audit(work_path, audit)
            raise RuntimeError("PANORAMAX_NO_COVERAGE: no licensed imagery found in the requested circle")
        audit["coverage_status"] = "complete"
        _write_audit(work_path, audit)
        return panos
    except Exception:
        if not (work_path / "pano-crawl.json").exists():
            _write_audit(work_path, audit)
        raise


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            # Camera JPEGs can carry MPF metadata and be decoded as MPO by PIL.
            if image.format not in {"JPEG", "MPO"}:
                return None
            return tuple(map(int, image.size))
    except (OSError, ValueError):
        return None


def _valid_image(path: Path, *, panoramic: bool) -> bool:
    if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        return False
    dimensions = _image_dimensions(path)
    if dimensions is None:
        return False
    if panoramic:
        width, height = dimensions
        return height > 0 and abs((width / height) - 2.0) <= 0.02
    return True


def _read_image_response(response: Any) -> bytes:
    try:
        response.raise_for_status()
    except Exception:
        raise
    headers = getattr(response, "headers", {}) or {}
    try:
        content_length = int(headers.get("content-length", "0") or 0)
    except (TypeError, ValueError):
        content_length = 0
    if content_length > MAX_IMAGE_BYTES:
        raise ValueError("image exceeds 20 MiB limit")

    chunks: list[bytes] = []
    total = 0
    iterator_factory = getattr(response, "iter_content", None)
    iterator = iterator_factory(chunk_size=64 * 1024) if callable(iterator_factory) else [getattr(response, "content", b"")]
    for chunk in iterator:
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise ValueError("image exceeds 20 MiB limit")
        chunks.append(bytes(chunk))
    return b"".join(chunks)


def _download_image(url: str, destination: Path, *, panoramic: bool) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    current_url = _trusted_https_url(url)
    if current_url is None:
        raise ValueError("Panoramax asset URL is not an allowed HTTPS origin")
    for _ in range(4):
        response = requests.get(
            current_url,
            headers={"User-Agent": "Netryx-Web/1.0"},
            stream=True,
            allow_redirects=False,
            timeout=60,
        )
        status = int(getattr(response, "status_code", 200))
        if 300 <= status < 400:
            location = (getattr(response, "headers", {}) or {}).get("location")
            redirected = _trusted_https_url(urljoin(current_url, location or ""))
            if redirected is None:
                raise ValueError("Panoramax asset redirected to an untrusted origin")
            current_url = redirected
            continue
        data = _read_image_response(response)
        content_type = str((getattr(response, "headers", {}) or {}).get("content-type", ""))
        if content_type and content_type.split(";", 1)[0].strip().lower() not in {"image/jpeg", "image/jpg"}:
            raise ValueError("Panoramax asset is not JPEG")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=destination.parent, prefix=f".{destination.stem}-", suffix=".tmp", delete=False
            ) as handle:
                handle.write(data)
                handle.flush()
                temporary = Path(handle.name)
            if not _valid_image(temporary, panoramic=panoramic):
                raise ValueError("Panoramax asset failed JPEG or panorama validation")
            temporary.replace(destination)
            temporary = None
            return destination
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    raise ValueError("too many Panoramax redirects")


def _ensure_cached_image(pano: dict[str, Any], work_dir: Path, *, panoramic: bool) -> Path:
    panoid = str(pano["panoid"])
    try:
        safe_id = str(uuid.UUID(panoid))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid panorama UUID") from exc
    folder = "panoramax-images-hd" if pano.get("image_variant") == "hd" else "panoramax-images"
    destination = work_dir / folder / f"{safe_id}.jpg"
    if _valid_image(destination, panoramic=panoramic):
        return destination
    if destination.exists():
        destination.unlink()
    image_url = pano.get("image_url") or pano.get("href") or pano.get("url")
    if not image_url:
        raise ValueError("Panoramax pano has no image URL")
    return _download_image(str(image_url), destination, panoramic=panoramic)


def _declared_panoramic(pano: dict[str, Any]) -> bool:
    if pano.get("perspective") is False:
        return True
    if pano.get("perspective") is True:
        return False
    field_of_view = pano.get("field_of_view")
    try:
        return field_of_view is not None and math.isclose(float(field_of_view), 360.0, abs_tol=0.01)
    except (TypeError, ValueError):
        return False


def _is_panoramic(pano: dict[str, Any], path: Path) -> bool:
    perspective = pano.get("perspective")
    if perspective is not None:
        return perspective is False
    field_of_view = pano.get("field_of_view")
    try:
        if field_of_view is not None:
            return math.isclose(float(field_of_view), 360.0, abs_tol=0.01)
    except (TypeError, ValueError):
        pass
    dimensions = _image_dimensions(path)
    return bool(dimensions and dimensions[1] > 0 and abs((dimensions[0] / dimensions[1]) - 2.0) <= 0.02)


def _source_metadata(pano: dict[str, Any], path: Path, *, panoramic: bool, heading: float | None) -> dict[str, Any]:
    camera_azimuth = pano.get("camera_azimuth")
    try:
        camera_azimuth = float(camera_azimuth) % 360.0 if camera_azimuth is not None else None
    except (TypeError, ValueError):
        camera_azimuth = None
    metadata: dict[str, Any] = {
        "panoid": str(pano["panoid"]),
        "lat": float(pano["lat"]),
        "lon": float(pano["lon"]),
        "path": str(path),
        "perspective": not panoramic,
        "provider": pano.get("provider", "panoramax"),
        "source_url": pano.get("source_url"),
        "license": pano.get("license"),
        "license_url": pano.get("license_url"),
        "attribution": list(pano.get("attribution") or []),
        "datetime": pano.get("datetime"),
        "camera_azimuth": camera_azimuth,
        "pitch": pano.get("pitch"),
        "roll": pano.get("roll"),
        "heading_reference": "image_center" if panoramic else "geographic",
        "orientation_limitation": "pitch/roll not handled by shared_utils renderer" if panoramic else None,
    }
    if heading is not None:
        metadata["heading"] = heading
    else:
        metadata["heading"] = 0.0
        metadata["heading_reference"] = "unknown"
    return metadata


def _load_indexing_components() -> tuple[Any, Any, Any, Any, Any, Any]:
    """Load optional ML/projection dependencies at index-build time only.

    Keeping this seam separate lets web-only tests exercise the provider with a
    small fake projection/model without importing torch at collection time.
    """
    import torch

    from cosplace_utils import batch_extract_cosplace
    from shared_utils import (
        equirectangular_to_rectilinear_torch,
        get_projection_base_dirs,
        pil_to_tensor,
        tensor_to_pil,
    )

    return (
        torch,
        equirectangular_to_rectilinear_torch,
        get_projection_base_dirs,
        pil_to_tensor,
        tensor_to_pil,
        batch_extract_cosplace,
    )


def build_index(
    panos: list[dict[str, Any]], work_dir: str | Path, headings: Iterable[float]
) -> tuple[Any, list[dict[str, Any]], int]:
    """Download licensed imagery and build the CosPlace descriptor index."""
    import numpy as np
    from PIL import Image

    if not panos:
        raise RuntimeError("PANORAMAX_NO_USABLE_IMAGES: no licensed Panoramax images were discovered")
    if len(panos) > _max_images():
        raise RuntimeError(
            f"PANORAMAX_MAX_IMAGES_EXCEEDED: {len(panos)} images exceed the configured bound; narrow radius"
        )
    heading_values = [float(value) for value in headings]
    if not heading_values:
        raise ValueError("headings must not be empty")

    (
        torch,
        equirectangular_to_rectilinear_torch,
        get_projection_base_dirs,
        pil_to_tensor,
        tensor_to_pil,
        batch_extract_cosplace,
    ) = _load_indexing_components()

    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    descriptors: list[Any] = []
    metadata: list[dict[str, Any]] = []
    downloaded = 0
    skipped = Counter()
    errors: list[dict[str, str]] = []

    for pano in panos:
        try:
            # A declared 360-degree image is validated as a 2:1 equirectangular
            # before it can reach the projection code.
            declared_panoramic = _declared_panoramic(pano)
            path = _ensure_cached_image(pano, root, panoramic=bool(declared_panoramic))
            panoramic = _is_panoramic(pano, path)
            if panoramic and not _valid_image(path, panoramic=True):
                raise ValueError("invalid equirectangular panorama")
            with Image.open(path) as original:
                image = original.convert("RGB")

            if panoramic:
                pano_tensor = pil_to_tensor(image)
                base_dirs = get_projection_base_dirs(90, (512, 512))
                local_headings = [value % 360.0 for value in heading_values]
                with torch.no_grad():
                    crops = equirectangular_to_rectilinear_torch(
                        pano_tensor, 90, (512, 512), local_headings, 0, base_dirs
                    )
                crop_images = [tensor_to_pil(crops[index : index + 1]).convert("RGB") for index in range(len(local_headings))]
                rows = []
                for local_heading in local_headings:
                    rows.append(_source_metadata(pano, path, panoramic=True, heading=local_heading))
            else:
                crop_images = [image.resize((512, 512), Image.Resampling.BILINEAR)]
                geographic_heading = pano.get("camera_azimuth", pano.get("heading"))
                try:
                    geographic_heading = float(geographic_heading) % 360.0
                except (TypeError, ValueError):
                    geographic_heading = None
                rows = [_source_metadata(pano, path, panoramic=False, heading=geographic_heading)]

            batch = np.asarray(batch_extract_cosplace(crop_images, batch_size=8), dtype=np.float32)
            if batch.ndim == 1:
                batch = batch.reshape(1, -1)
            if len(batch) != len(rows):
                raise ValueError("descriptor extractor returned an unexpected number of rows")
            for descriptor, row in zip(batch, rows):
                descriptor = np.asarray(descriptor, dtype=np.float32)
                descriptor /= np.linalg.norm(descriptor) + 1e-8
                descriptors.append(descriptor)
                metadata.append(row)
            downloaded += 1
            for crop in crop_images:
                crop.close()
            image.close()
        except Exception as exc:
            skipped[type(exc).__name__] += 1
            errors.append({"panoid": str(pano.get("panoid", "")), "message": str(exc)[:400]})
            continue

    try:
        audit_path = root / "pano-crawl.json"
        if audit_path.exists():
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["index_status"] = "failed" if not descriptors else "partial" if skipped else "complete"
            audit["downloaded_count"] = downloaded
            audit["indexed_descriptor_count"] = len(metadata)
            audit["index_skipped_counts"] = dict(skipped)
            audit["index_errors"] = errors
            _write_audit(root, audit)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    if not descriptors:
        raise RuntimeError(
            "PANORAMAX_NO_USABLE_IMAGES: no usable licensed Panoramax images could be indexed; see pano-crawl.json"
        )
    array = np.asarray(descriptors, dtype=np.float32)
    np.savez_compressed(
        root / "global-index.npz",
        descriptors=array,
        metadata=np.asarray(metadata, dtype=object),
    )
    return array, metadata, downloaded
