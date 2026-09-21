from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .research import google_maps_url, reverse_geocode_ban
    from .photo_selection import ExteriorClassifier, select_exterior_images
except ImportError:  # direct `python /path/live_runner.py` execution
    from netryx_web.research import google_maps_url, reverse_geocode_ban
    from netryx_web.photo_selection import ExteriorClassifier, select_exterior_images


def haversine_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    radius = 6_371_000.0
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(
        (lon2 - lon1) / 2
    ) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def grid_points(
    center: tuple[float, float], radius_m: float, *, grid_size: int = 9
) -> list[tuple[float, float]]:
    """Return a square sampling grid centered on the requested search center."""
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    if grid_size < 3 or grid_size % 2 == 0:
        raise ValueError("grid_size must be an odd integer >= 3")
    latitude, longitude = center
    lat_delta = radius_m / 111_320.0
    lon_delta = radius_m / (111_320.0 * max(math.cos(math.radians(latitude)), 0.1))
    middle = grid_size // 2
    points: list[tuple[float, float]] = []
    for row in range(grid_size):
        for column in range(grid_size):
            if row == middle and column == middle:
                points.append(center)
                continue
            points.append(
                (
                    latitude + ((row - middle) / middle) * lat_delta,
                    longitude + ((column - middle) / middle) * lon_delta,
                )
            )
    return points


async def _crawl_one(
    session: Any, point: tuple[float, float], semaphore: Any, api_key: str
) -> list[dict[str, Any]]:
    async with semaphore:
        url = (
            "https://maps.googleapis.com/maps/api/streetview/metadata"
            f"?location={point[0]:.8f},{point[1]:.8f}&radius=50&key={api_key}"
        )
        for attempt in range(3):
            try:
                async with session.get(url, timeout=45) as response:
                    if response.status == 200:
                        payload = await response.json(content_type=None)
                        if payload.get("status") != "OK":
                            return []
                        location = payload.get("location") or {}
                        pano_id = payload.get("pano_id")
                        if not pano_id:
                            return []
                        return [
                            {
                                "panoid": str(pano_id),
                                "lat": float(location["lat"]),
                                "lon": float(location["lng"]),
                            }
                        ]
            except Exception:
                pass
            await asyncio.sleep(0.5 * (attempt + 1))
    return []


async def _crawl_async(
    center: tuple[float, float], radius_m: float, grid_size: int, api_key: str
) -> list[dict[str, Any]]:
    import aiohttp

    points = grid_points(center, radius_m, grid_size=grid_size)
    semaphore = asyncio.Semaphore(min(24, len(points)))
    connector = aiohttp.TCPConnector(limit=min(24, len(points)))
    async with aiohttp.ClientSession(
        connector=connector, headers={"User-Agent": "Netryx-Web/1.0"}
    ) as session:
        batches = await asyncio.gather(
            *(_crawl_one(session, point, semaphore, api_key) for point in points)
        )
    unique: dict[str, dict[str, Any]] = {}
    for batch in batches:
        for pano in batch:
            pano_id = str(pano["panoid"])
            current = unique.get(pano_id)
            if current is None or (current.get("lat") is None and pano.get("lat") is not None):
                unique[pano_id] = pano
    return [
        pano
        for pano in unique.values()
        if pano.get("lat") is not None and pano.get("lon") is not None
    ]


def _authorized_streetview_key() -> str:
    api_key = os.environ.get("GOOGLE_STREETVIEW_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GOOGLE_STREETVIEW_AUTH_REQUIRED: Street View metadata requires an API key")
    if os.environ.get("NETRYX_GOOGLE_STREETVIEW_AUTHORIZED", "").lower() != "true":
        raise RuntimeError(
            "GOOGLE_STREETVIEW_USE_NOT_AUTHORIZED: confirm that this imagery workflow is licensed"
        )
    return api_key


def imagery_provider() -> str:
    provider = os.environ.get("NETRYX_IMAGERY_PROVIDER", "panoramax").strip().lower()
    if provider not in {"panoramax", "google"}:
        raise ValueError("NETRYX_IMAGERY_PROVIDER must be panoramax or google")
    return provider


def crawl_panos(center: tuple[float, float], radius_m: float, work_dir: Path) -> list[dict[str, Any]]:
    if imagery_provider() == "panoramax":
        from netryx_web.panoramax import discover

        return discover([center], radius_m, work_dir)
    api_key = _authorized_streetview_key()
    panos = asyncio.run(
        _crawl_async(
            center,
            radius_m,
            int(os.environ.get("NETRYX_GRID_SIZE", "9")),
            api_key,
        )
    )
    (work_dir / "pano-crawl.json").write_text(
        json.dumps({"center": center, "radius_m": radius_m, "panos": panos}, indent=2),
        encoding="utf-8",
    )
    return panos


def crawl_panos_many(
    centers: list[tuple[float, float]], radius_m: float, work_dir: Path
) -> list[dict[str, Any]]:
    """Crawl every evidence-backed center, not just the displayed commune."""
    if imagery_provider() == "panoramax":
        from netryx_web.panoramax import discover

        return discover(centers, radius_m, work_dir)
    api_key = _authorized_streetview_key()
    unique: dict[str, dict[str, Any]] = {}
    grid_size = int(os.environ.get("NETRYX_GRID_SIZE", "9"))
    for center in centers:
        for pano in asyncio.run(_crawl_async(center, radius_m, grid_size, api_key)):
            pano_id = str(pano["panoid"])
            current = unique.get(pano_id)
            if current is None or (current.get("lat") is None and pano.get("lat") is not None):
                unique[pano_id] = pano
    panos = list(unique.values())
    (work_dir / "pano-crawl.json").write_text(
        json.dumps({"centers": centers, "radius_m": radius_m, "panos": panos}, indent=2),
        encoding="utf-8",
    )
    return panos


def query_images(
    manifest: dict[str, Any],
    *,
    limit: int = 4,
    classifier: ExteriorClassifier | Any | None = None,
    audit_path: str | Path | None = None,
) -> list[Path]:
    selected, _ = select_exterior_images(
        manifest,
        limit=limit,
        classifier=classifier,
        audit_path=audit_path,
    )
    return selected


def aggregate_verified_candidates(
    rows: Iterable[dict[str, Any]], *, min_inliers: int = 8
) -> list[dict[str, Any]]:
    """Aggregate only sufficiently verified, unique query/panorama matches."""
    best_by_pano_image: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        inliers = int(row["inliers"])
        if inliers < min_inliers:
            continue
        key = (str(row["panoid"]), str(row["image"]))
        current = best_by_pano_image.get(key)
        if current is None or (inliers, int(row["raw_matches"])) > (
            int(current["inliers"]),
            int(current["raw_matches"]),
        ):
            best_by_pano_image[key] = row

    grouped: dict[str, dict[str, Any]] = {}
    for row in best_by_pano_image.values():
        pano_id = str(row["panoid"])
        current = grouped.setdefault(
            pano_id,
            {
                "panoid": pano_id,
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "sources": [],
            },
        )
        current["sources"].append(
            {
                "image": row["image"],
                "heading": float(row["heading"]),
                "inliers": int(row["inliers"]),
                "raw_matches": int(row["raw_matches"]),
            }
        )
    for candidate in grouped.values():
        sources = candidate["sources"]
        candidate["query_support"] = len({source["image"] for source in sources})
        candidate["sum_inliers"] = sum(source["inliers"] for source in sources)
        candidate["best_inliers"] = max(source["inliers"] for source in sources)
        candidate["best_raw_matches"] = max(source["raw_matches"] for source in sources)
    return sorted(
        grouped.values(),
        key=lambda item: (
            item["query_support"] >= 2,
            item["sum_inliers"],
            item["best_inliers"],
            item["best_raw_matches"],
        ),
        reverse=True,
    )


def build_result(
    candidate: dict[str, Any],
    *,
    panos_indexed: int,
    views_tested: int,
    reverse_geocode: Any = reverse_geocode_ban,
) -> dict[str, Any]:
    latitude = float(candidate["lat"])
    longitude = float(candidate["lon"])
    label = reverse_geocode(latitude, longitude) or f"{latitude:.6f}, {longitude:.6f}"
    support = int(candidate["query_support"])
    best_inliers = int(candidate["best_inliers"])
    level = "HIGH" if support >= 2 and best_inliers >= 40 else "MEDIUM" if best_inliers >= 15 else "LOW"
    return {
        "summary_fr": (
            f"La meilleure correspondance d’imagerie de rue se situe près de {label}. "
            "La position est une estimation de caméra à vérifier avant tout déplacement."
        ),
        "location": {"label_fr": label, "latitude": latitude, "longitude": longitude},
        "confidence": {"level": level, "score": None},
        "evidence": [
            {
                "kind": "geometric_match",
                "label_fr": "Correspondances géométriques",
                "detail_fr": (
                    f"{int(candidate['sum_inliers'])} correspondances RANSAC cumulées "
                    f"sur {support} photo{'s' if support > 1 else ''}."
                ),
                "value": int(candidate["sum_inliers"]),
                "unit": "inliers",
            },
            {
                "kind": "streetview_index",
                "label_fr": "Couverture d’imagerie de rue",
                "detail_fr": f"{panos_indexed} images de rue indexées ; {views_tested} comparaisons retenues.",
                "value": panos_indexed,
                "unit": "panoramas",
            },
            {
                "kind": "camera_position",
                "label_fr": "Position de caméra",
                "detail_fr": "Position de prise de vue, pas une adresse de bâtiment confirmée.",
                "value": None,
                "unit": None,
            },
        ],
        "google_maps_url": google_maps_url(latitude, longitude),
    }


def _geocode_location(location_hint: str | None) -> tuple[float, float] | None:
    if not location_hint:
        return None
    import requests

    try:
        response = requests.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": location_hint, "limit": 1},
            headers={"User-Agent": "Netryx-Web/1.0"},
            timeout=12,
        )
        response.raise_for_status()
        features = response.json().get("features") or []
        coordinates = features[0]["geometry"]["coordinates"]
        return float(coordinates[1]), float(coordinates[0])
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
        return None


def _geocode_location_candidates(location_hints: Iterable[str]) -> list[tuple[float, float]]:
    """Return distinct geocoder candidates from primary and mirror hints."""
    import requests

    candidates: list[tuple[float, float]] = []
    seen: set[tuple[float, float]] = set()
    for hint in location_hints:
        if not isinstance(hint, str) or not hint.strip():
            continue
        try:
            response = requests.get(
                "https://api-adresse.data.gouv.fr/search/",
                params={"q": hint, "limit": 8},
                headers={"User-Agent": "Netryx-Web/1.0"},
                timeout=12,
            )
            response.raise_for_status()
            for feature in response.json().get("features") or []:
                coordinates = feature["geometry"]["coordinates"]
                candidate = (float(coordinates[1]), float(coordinates[0]))
                key = (round(candidate[0], 5), round(candidate[1], 5))
                if key not in seen:
                    seen.add(key)
                    candidates.append(candidate)
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
            continue
    return candidates


def _manifest_location_hints(manifest: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for value in manifest.get("location_hints") or []:
        if isinstance(value, str) and value.strip() and value not in hints:
            hints.append(value)
    location_hint = manifest.get("location_hint")
    if isinstance(location_hint, str) and location_hint.strip() and location_hint not in hints:
        hints.append(location_hint)
    syndication = manifest.get("syndication") or {}
    for candidate in syndication.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("location_hint")
        if isinstance(value, str) and value.strip() and value not in hints:
            hints.append(value)
    return hints


def _manifest_centers(manifest: dict[str, Any]) -> list[tuple[float, float]]:
    centers: list[tuple[float, float]] = []
    center_payload = manifest.get("center") or {}
    if center_payload:
        try:
            centers.append((float(center_payload["latitude"]), float(center_payload["longitude"])))
        except (KeyError, TypeError, ValueError):
            pass
    for center in _geocode_location_candidates(_manifest_location_hints(manifest)):
        if all(haversine_m(center, existing) > 75 for existing in centers):
            centers.append(center)
    return centers


def build_static_view_url(panoid: str, heading: float, api_key: str) -> str:
    from urllib.parse import urlencode

    return "https://maps.googleapis.com/maps/api/streetview?" + urlencode(
        {
            "size": "640x640",
            "pano": panoid,
            "heading": f"{heading:.3f}",
            "fov": "90",
            "pitch": "0",
            "key": api_key,
        }
    )


def _download_static_view(
    pano: dict[str, Any], heading: float, view_dir: Path, api_key: str
) -> Path | None:
    import requests

    view_dir.mkdir(parents=True, exist_ok=True)
    path = view_dir / f"{pano['panoid']}-{int(round(heading)) % 360}.jpg"
    if path.is_file():
        return path
    try:
        response = requests.get(
            build_static_view_url(str(pano["panoid"]), heading, api_key),
            timeout=45,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type != "image/jpeg":
            return None
        path.write_bytes(response.content)
        return path
    except (requests.RequestException, OSError):
        return None


def _build_static_index(
    panos: list[dict[str, Any]], work_dir: Path, headings: list[int], api_key: str
) -> tuple[Any, list[dict[str, Any]], int]:
    import numpy as np
    from PIL import Image
    from cosplace_utils import batch_extract_cosplace

    view_dir = work_dir / "streetview-views"
    descriptors: list[Any] = []
    metadata: list[dict[str, Any]] = []
    downloaded = 0
    for pano in panos:
        images: list[tuple[int, Path]] = []
        for heading in headings:
            path = _download_static_view(pano, heading, view_dir, api_key)
            if path is not None:
                images.append((heading, path))
        if not images:
            continue
        downloaded += 1
        crop_images = []
        for _, path in images:
            with Image.open(path) as image:
                crop_images.append(image.convert("RGB").resize((512, 512), Image.Resampling.BILINEAR))
        batch = batch_extract_cosplace(crop_images, batch_size=8).astype(np.float32)
        for (heading, path), descriptor in zip(images, batch):
            descriptor /= np.linalg.norm(descriptor) + 1e-8
            metadata.append(
                {
                    "panoid": pano["panoid"],
                    "heading": heading,
                    "lat": float(pano["lat"]),
                    "lon": float(pano["lon"]),
                    "path": str(path),
                    "perspective": True,
                }
            )
            descriptors.append(descriptor)
        for image in crop_images:
            image.close()
    if not descriptors:
        raise RuntimeError(
            "Google Street View Static API returned no usable images; check the API key and billing"
        )
    array = np.asarray(descriptors, dtype=np.float32)
    np.savez_compressed(
        work_dir / "global-index.npz",
        descriptors=array,
        metadata=np.asarray(metadata, dtype=object),
    )
    return array, metadata, downloaded


def _build_global_index(
    panos: list[dict[str, Any]], work_dir: Path, headings: list[int]
) -> tuple[Any, list[dict[str, Any]], int]:
    if imagery_provider() == "panoramax":
        from netryx_web.panoramax import build_index

        return build_index(panos, work_dir, headings)
    api_key = _authorized_streetview_key()
    return _build_static_index(panos, work_dir, headings, api_key)


def _global_search(
    query_paths: list[Path], descriptors: Any, metadata: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    import numpy as np
    from PIL import Image
    from cosplace_utils import extract_cosplace_descriptor

    rows: list[dict[str, Any]] = []
    for query_path in query_paths:
        with Image.open(query_path) as original:
            image = original.convert("RGB").resize((512, 512), Image.Resampling.BILINEAR)
            crop = image.crop((51, 51, 461, 461)).resize((512, 512), Image.Resampling.BILINEAR)
            variants = [
                ("original", image),
                ("cropped", crop),
                ("flipped", image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)),
            ]
            best_by_pano: dict[str, dict[str, Any]] = {}
            for variant, picture in variants:
                descriptor = extract_cosplace_descriptor(picture)
                scores = descriptors @ descriptor
                seen_panos: set[str] = set()
                for index in np.argsort(scores)[::-1]:
                    pano_id = str(metadata[int(index)]["panoid"])
                    if pano_id in seen_panos:
                        continue
                    if len(seen_panos) >= top_k:
                        break
                    seen_panos.add(pano_id)
                    item = dict(metadata[int(index)])
                    item.update(
                        {
                            "image": query_path.name,
                            "descriptor": variant,
                            "score": float(scores[int(index)]),
                        }
                    )
                    pano_id = str(item["panoid"])
                    if pano_id not in best_by_pano or item["score"] > best_by_pano[pano_id]["score"]:
                        best_by_pano[pano_id] = item
            rows.extend(best_by_pano.values())
            variants[-1][1].close()
            crop.close()
            image.close()
    return rows


def _ransac_inliers(
    first: Any, second: Any, matches: Any, *, min_matches: int = 6
) -> tuple[int, int]:
    import cv2
    import numpy as np

    valid = matches > -1
    raw = int(valid.sum())
    if raw < min_matches:
        return raw, 0
    try:
        matrix, mask = cv2.findHomography(
            first[np.where(valid)[0]].astype(float),
            second[matches[valid]].astype(float),
            cv2.RANSAC,
            5.0,
        )
        if matrix is None or mask is None:
            return raw, 0
        return raw, int(mask.sum())
    except (cv2.error, ValueError, TypeError):
        return raw, 0


def _verify(
    query_paths: list[Path], global_rows: list[dict[str, Any]], max_candidates: int
) -> list[dict[str, Any]]:
    import torch
    from PIL import Image
    from lightglue import DISK, LightGlue
    from shared_utils import equirectangular_to_rectilinear_torch, get_projection_base_dirs, pil_to_tensor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    extractor = DISK(max_num_keypoints=768).eval().to(device)
    matcher = LightGlue(
        features="disk",
        depth_confidence=0.9,
        width_confidence=0.95,
        n_layers=6,
        flash=device == "cuda",
    ).eval().to(device)
    rows: list[dict[str, Any]] = []
    by_image: dict[str, list[dict[str, Any]]] = {}
    for row in global_rows:
        by_image.setdefault(str(row["image"]), []).append(row)
    offsets = [-30, 0, 30]
    for query_path in query_paths:
        query_rows = by_image.get(query_path.name, [])
        candidates: dict[str, dict[str, Any]] = {}
        for row in sorted(query_rows, key=lambda item: item["score"], reverse=True):
            candidates.setdefault(str(row["panoid"]), row)
        with Image.open(query_path) as image:
            query = image.convert("RGB").resize((512, 512), Image.Resampling.BILINEAR)
        query_features = extractor.extract(pil_to_tensor(query))
        for candidate in list(candidates.values())[:max_candidates]:
            if candidate.get("perspective"):
                with Image.open(candidate["path"]) as candidate_image:
                    candidate_view = candidate_image.convert("RGB").resize(
                        (512, 512), Image.Resampling.BILINEAR
                    )
                candidate_features = extractor.extract(pil_to_tensor(candidate_view))
                with torch.no_grad():
                    matches = matcher({"image0": query_features, "image1": candidate_features})
                match_array = matches["matches0"][0].detach().cpu().numpy()
                raw, inliers = _ransac_inliers(
                    query_features["keypoints"][0].detach().cpu().numpy(),
                    candidate_features["keypoints"][0].detach().cpu().numpy(),
                    match_array,
                )
                rows.append(
                    {
                        "panoid": candidate["panoid"],
                        "lat": candidate["lat"],
                        "lon": candidate["lon"],
                        "image": query_path.name,
                        "heading": candidate["heading"],
                        "raw_matches": raw,
                        "inliers": inliers,
                    }
                )
                candidate_view.close()
                del candidate_features
                continue
            pano = Image.open(candidate["path"]).convert("RGB")
            pano_tensor = pil_to_tensor(pano)
            headings = [(float(candidate["heading"]) + offset) % 360 for offset in offsets]
            base_dirs = get_projection_base_dirs(90, (512, 512))
            with torch.no_grad():
                crops = equirectangular_to_rectilinear_torch(
                    pano_tensor, 90, (512, 512), headings, 0, base_dirs
                )
            best_for_candidate: dict[str, Any] | None = None
            for index, heading in enumerate(headings):
                with torch.no_grad():
                    features = extractor.extract(crops[index : index + 1])
                    matches = matcher({"image0": query_features, "image1": features})
                match_array = matches["matches0"][0].detach().cpu().numpy()
                raw, inliers = _ransac_inliers(
                    query_features["keypoints"][0].detach().cpu().numpy(),
                    features["keypoints"][0].detach().cpu().numpy(),
                    match_array,
                )
                current = {
                    "panoid": candidate["panoid"],
                    "lat": candidate["lat"],
                    "lon": candidate["lon"],
                    "image": query_path.name,
                    "heading": heading,
                    "raw_matches": raw,
                    "inliers": inliers,
                }
                if best_for_candidate is None or (inliers, raw) > (
                    best_for_candidate["inliers"],
                    best_for_candidate["raw_matches"],
                ):
                    best_for_candidate = current
            if best_for_candidate is not None:
                rows.append(best_for_candidate)
            pano.close()
            del pano_tensor, crops
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        query.close()
        del query_features
    del matcher, extractor
    return rows


def _run(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    work_dir = manifest_path.parent
    query_paths = query_images(
        manifest,
        limit=int(os.environ.get("NETRYX_MAX_QUERY_IMAGES", "4")),
        audit_path=work_dir / "exterior-selection.json",
    )
    if not query_paths:
        raise RuntimeError("NO_HIGH_CONFIDENCE_EXTERIOR_PHOTOS: review required")
    centers = _manifest_centers(manifest)
    if not centers:
        raise RuntimeError("INSUFFICIENT_GEO_HINT: no evidence-backed city or nearby city")

    radius_m = float(manifest.get("search_radius_m", os.environ.get("NETRYX_SEARCH_RADIUS_M", "300")))
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("search_radius_m must be finite and positive")
    headings = list(range(0, 360, int(os.environ.get("NETRYX_INDEX_HEADING_STEP", "45"))))
    panos = crawl_panos_many(centers, radius_m, work_dir)
    if not panos:
        raise RuntimeError("NO_IMAGERY_COVERAGE: no usable street imagery around the search center")
    descriptors, metadata, downloaded = _build_global_index(panos, work_dir, headings)
    global_rows = _global_search(
        query_paths,
        descriptors,
        metadata,
        int(os.environ.get("NETRYX_GLOBAL_TOP_K", "20")),
    )
    verified_rows = _verify(
        query_paths,
        global_rows,
        int(os.environ.get("NETRYX_VERIFY_TOP_PANOS", "8")),
    )
    minimum = max(8, int(os.environ.get("NETRYX_MIN_INLIERS", "8")))
    ranked = aggregate_verified_candidates(verified_rows, min_inliers=minimum)
    (work_dir / "search-evidence.json").write_text(json.dumps({
        "provider": imagery_provider(), "centers": centers, "radius_m": radius_m,
        "query_images": [str(path) for path in query_paths],
        "images_indexed": downloaded, "index_views": len(metadata), "min_inliers": minimum,
        "global_candidates": global_rows, "verified": verified_rows, "ranked": ranked,
        "status": "candidate_found" if ranked else "no_match",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if not ranked:
        raise RuntimeError("NO_GEOMETRIC_MATCH: no sufficiently reliable geometric match; see search-evidence.json")
    result = build_result(
        ranked[0],
        panos_indexed=downloaded,
        views_tested=len(verified_rows),
    )
    winner = next((pano for pano in panos if pano["panoid"] == ranked[0]["panoid"]), {})
    if imagery_provider() == "panoramax":
        attribution = winner.get("attribution") or []
        if isinstance(attribution, list):
            attribution = ", ".join(attribution)
        result["evidence"].append({
            "kind": "imagery_source", "label_fr": "Source et licence Panoramax",
            "detail_fr": (
                f"Panoramax · {attribution} · {winner.get('license', '')} · "
                f"{winner.get('source_url', '')} · {winner.get('license_url', '')}"
            ),
            "value": None, "unit": None,
        })
    result["evidence"].append(
        {
            "kind": "listing_source",
            "label_fr": "Source de l’annonce",
            "detail_fr": (
                f"{manifest.get('acquisition', 'unknown')} · {len(query_paths)} photo(s) extérieures "
                f"traitée(s) sur {len(centers)} zone(s) candidate(s)."
            ),
            "value": len(query_paths),
            "unit": "photos",
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: live_runner.py MANIFEST_PATH", file=sys.stderr)
        return 2
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = _run(Path(args[0]).resolve())
    except Exception as exc:
        print(f"live runner failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
