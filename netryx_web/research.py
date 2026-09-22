from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

from .syndication import SyndicationRequest, default_discoverer


ProgressCallback = Callable[[str, int | None, str], None]


class ResearchError(RuntimeError):
    def __init__(self, code: str, message_fr: str, *, retryable: bool = False):
        super().__init__(message_fr)
        self.code = code
        self.message_fr = message_fr
        self.retryable = retryable


def google_maps_url(latitude: float, longitude: float) -> str:
    query = quote(f"{latitude:.7f},{longitude:.7f}", safe="")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def structured_panoramax_sources(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    from .panoramax import safe_https_url, trusted_provenance_url

    structured: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    raw_sources = candidate.get("sources")
    source_items = list(raw_sources) if isinstance(raw_sources, list) else []
    if all(candidate.get(field) is not None for field in ("provider", "source_url", "license")):
        source_items.insert(0, candidate)
    for source in source_items:
        if not isinstance(source, dict):
            continue
        source_url = trusted_provenance_url(source.get("source_url"))
        provider = source.get("provider")
        license_name = source.get("license")
        if (
            source_url is None
            or not isinstance(provider, str)
            or provider.strip().lower() != "panoramax"
            or not isinstance(license_name, str)
            or not license_name.strip()
            or source_url in seen_urls
        ):
            continue
        seen_urls.add(source_url)
        item: dict[str, Any] = {
            "provider": "panoramax",
            "source_url": source_url,
            "license": license_name.strip(),
        }
        license_url = safe_https_url(source.get("license_url"))
        if license_url is not None:
            item["license_url"] = license_url
        attribution = source.get("attribution")
        if isinstance(attribution, list):
            cleaned = [value.strip() for value in attribution if isinstance(value, str) and value.strip()]
            if cleaned:
                item["attribution"] = cleaned
        structured.append(item)
    return structured


def project_point(latitude: float, longitude: float, heading: float, distance_m: float) -> tuple[float, float]:
    radius = 6_371_000.0
    bearing = math.radians(heading)
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    angular = distance_m / radius
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def circular_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    x = sum(math.cos(math.radians(value)) for value in values)
    y = sum(math.sin(math.radians(value)) for value in values)
    return math.degrees(math.atan2(y, x)) % 360


def reverse_geocode_ban(latitude: float, longitude: float) -> str | None:
    try:
        response = requests.get(
            "https://api-adresse.data.gouv.fr/reverse/",
            params={"lat": latitude, "lon": longitude},
            headers={"User-Agent": "Netryx-Web/1.0"},
            timeout=12,
        )
        response.raise_for_status()
        features = response.json().get("features", [])
        if not features:
            return None
        return features[0].get("properties", {}).get("label")
    except (requests.RequestException, ValueError, TypeError):
        return None


class ArtifactResearchPipeline:
    """Reads prior, reproducible Netryx outputs from a configurable cache.

    This is a cache adapter, not a fixture: coordinates and evidence are always
    derived from the artifact selected by the submitted listing identifier.
    """

    def __init__(
        self,
        results_root: str | Path,
        *,
        reverse_geocode: Callable[[float, float], str | None] = reverse_geocode_ban,
        facade_distance_m: float = 18.0,
    ):
        self.results_root = Path(results_root)
        self.reverse_geocode = reverse_geocode
        self.facade_distance_m = facade_distance_m

    @staticmethod
    def _listing_id(listing_url: str) -> str | None:
        candidates = re.findall(r"(?<!\d)(\d{6,})(?!\d)", listing_url)
        return candidates[-1] if candidates else None

    def run(
        self,
        listing_url: str,
        progress: ProgressCallback,
    ) -> dict[str, Any] | None:
        listing_id = self._listing_id(listing_url)
        if listing_id is None:
            return None
        artifact = self.results_root / listing_id / "headless" / "ranked_coordinates.json"
        if not artifact.is_file():
            return None

        progress("searching_index", 35, "Résultats Netryx existants détectés…")
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            top = payload["combined_ranked"][0]
            camera_lat = float(top["lat"])
            camera_lon = float(top["lon"])
            raw_sources = top.get("sources")
            if raw_sources is None:
                sources = []
            elif isinstance(raw_sources, list):
                sources = list(raw_sources)
            else:
                raise TypeError("invalid provenance sources container")
            if not all(isinstance(source, dict) for source in sources):
                raise TypeError("invalid provenance sources")
            headings = [float(source["heading"]) for source in sources if "heading" in source]
            if not all(math.isfinite(value) for value in (camera_lat, camera_lon, *headings)):
                raise ValueError("non-finite artifact coordinate or heading")
            query_support = int(top.get("query_support") or len(sources))
            sum_inliers = int(top.get("sum_inliers") or sum(int(s.get("inliers", 0)) for s in sources))
            best_inliers = int(top.get("best_inliers") or max((int(s.get("inliers", 0)) for s in sources), default=0))
            panoids_indexed = int(payload.get("panoids_indexed", 0))
            global_entries = int(payload.get("global_entries", 0))
        except (OSError, ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise ResearchError(
                "INVALID_RESEARCH_ARTIFACT",
                "Le résultat Netryx enregistré est incomplet ou illisible.",
            ) from exc

        progress("refining", 82, "Conversion des correspondances en position cartographique…")
        heading = circular_mean(headings)
        latitude, longitude = project_point(
            camera_lat,
            camera_lon,
            heading,
            self.facade_distance_m,
        )
        label = self.reverse_geocode(latitude, longitude)
        confidence = "HIGH" if query_support >= 2 and sum_inliers >= 80 and best_inliers >= 40 else "MEDIUM"
        location_label = label or f"{latitude:.6f}, {longitude:.6f}"

        evidence = [
            {
                "kind": "geometric_match",
                "label_fr": "Correspondances géométriques",
                "detail_fr": (
                    f"{sum_inliers} correspondances RANSAC cumulées sur "
                    f"{query_support} photo{'s' if query_support > 1 else ''}."
                ),
                "value": sum_inliers,
                "unit": "inliers",
            },
            {
                "kind": "streetview_index",
                "label_fr": "Couverture Street View",
                "detail_fr": (
                    f"{panoids_indexed} panoramas et "
                    f"{global_entries} vues ont été comparés."
                ),
                "value": panoids_indexed,
                "unit": "panoramas",
            },
            {
                "kind": "camera_projection",
                "label_fr": "Projection vers la façade",
                "detail_fr": (
                    "La coordonnée du panorama a été projetée dans la direction "
                    "visuelle vérifiée, puis rapprochée d’une adresse publique."
                ),
                "value": self.facade_distance_m,
                "unit": "m",
            },
        ]
        structured_sources = structured_panoramax_sources(top)
        result = {
            "summary_fr": (
                f"La meilleure correspondance place le bien près de {location_label}. "
                "La position est une estimation technique à vérifier avant tout déplacement."
            ),
            "location": {
                "label_fr": location_label,
                "latitude": latitude,
                "longitude": longitude,
            },
            "confidence": {"level": confidence, "score": None},
            "evidence": evidence,
            "google_maps_url": google_maps_url(latitude, longitude),
            "sources": structured_sources,
        }
        panoramax_source = next(
            (source for source in structured_sources if source["provider"].lower() == "panoramax"),
            None,
        )
        if panoramax_source is not None:
            result["panoramax_url"] = panoramax_source["source_url"]
        return result


class CommandResearchPipeline:
    """Runs a configured headless research command against a dynamic manifest."""

    def __init__(self, command: str, work_root: str | Path, listing_fetcher: Any):
        self.command = shlex.split(command)
        self.work_root = Path(work_root)
        self.listing_fetcher = listing_fetcher
        if not self.command:
            raise ValueError("research command is empty")

    def run(self, listing_url: str, progress: ProgressCallback) -> dict[str, Any]:
        digest = hashlib.sha256(listing_url.encode("utf-8")).hexdigest()[:20]
        work_dir = self.work_root / digest
        work_dir.mkdir(parents=True, exist_ok=True)
        progress("fetching_listing", 12, "Récupération des informations publiques de l’annonce…")
        manifest = self.listing_fetcher.fetch(listing_url, work_dir)
        manifest_path = work_dir / "listing-manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        progress("extracting_query", 28, "Préparation des photos de l’annonce…")
        try:
            completed = subprocess.run(
                [*self.command, str(manifest_path)],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=3_600,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ResearchError(
                "RESEARCH_TIMEOUT",
                "La recherche a dépassé la durée maximale autorisée.",
                retryable=True,
            ) from exc
        if completed.returncode != 0:
            diagnostic = completed.stderr.strip().splitlines()[-1:] or ["erreur inconnue"]
            raise ResearchError(
                "RESEARCH_COMMAND_FAILED",
                f"Le moteur Netryx a interrompu la recherche : {diagnostic[0][:240]}",
                retryable=True,
            )
        try:
            result = json.loads(completed.stdout)
            _validate_result(result)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            raise ResearchError(
                "INVALID_RESEARCH_OUTPUT",
                "Le moteur Netryx a renvoyé un résultat invalide.",
            ) from exc
        result["provenance"] = {
            "research_mode": "live",
            "submitted_url": listing_url,
            "syndication": manifest.get("syndication"),
            "location_hints": manifest.get("location_hints") or [],
        }
        return result


def _validate_result(result: dict[str, Any]) -> None:
    location = result["location"]
    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("invalid coordinates")
    result["google_maps_url"] = google_maps_url(latitude, longitude)


class ResearchService:
    def __init__(
        self,
        artifact_pipeline: ArtifactResearchPipeline,
        live_pipeline: CommandResearchPipeline | None = None,
        syndication_discoverer: Any | None = None,
    ):
        self.artifact_pipeline = artifact_pipeline
        self.live_pipeline = live_pipeline
        self.syndication_discoverer = syndication_discoverer or default_discoverer()

    def run(self, listing_url: str, progress: ProgressCallback) -> dict[str, Any]:
        cached = self.artifact_pipeline.run(listing_url, progress)
        if cached is not None:
            progress("checking_syndication", 90, "Vérification des copies publiques de l’annonce…")
            listing_id = ArtifactResearchPipeline._listing_id(listing_url)
            report = self.syndication_discoverer.discover(
                SyndicationRequest(submitted_url=listing_url, listing_id=listing_id)
            )
            cached["provenance"] = {
                "research_mode": "replay",
                "submitted_url": listing_url,
                "syndication": report,
            }
            return cached
        if self.live_pipeline is None:
            raise ResearchError(
                "PIPELINE_NOT_CONFIGURED",
                (
                    "Aucun résultat existant ne correspond à cette annonce et le pipeline "
                    "de recherche à la demande n’est pas configuré sur ce serveur."
                ),
                retryable=False,
            )
        return self.live_pipeline.run(listing_url, progress)


def build_research_service(
    *,
    results_root: str | Path,
    work_root: str | Path,
    listing_fetcher: Any,
) -> ResearchService:
    artifact = ArtifactResearchPipeline(results_root)
    command = os.environ.get("NETRYX_RESEARCH_COMMAND", "").strip()
    if not command:
        runner = Path(__file__).with_name("live_runner.py")
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(runner))}"
    live = CommandResearchPipeline(command, work_root, listing_fetcher) if command else None
    discoverer = getattr(listing_fetcher, "syndication_discoverer", None)
    return ResearchService(artifact, live, syndication_discoverer=discoverer)
