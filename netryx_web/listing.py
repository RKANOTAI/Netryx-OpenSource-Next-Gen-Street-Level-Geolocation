from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import os
import socket
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from .research import ResearchError
from .security import UnsafeUrlError, validate_listing_url
from .syndication import (
    SyndicationDiscoverer,
    SyndicationRequest,
    default_discoverer,
    error_report,
    request_from_manifest,
)


HTML_LIMIT = 8 * 1024 * 1024
IMAGE_LIMIT = 12 * 1024 * 1024
MAX_IMAGES = 16


def _assert_public_dns(url: str) -> None:
    hostname = urlsplit(url).hostname
    if not hostname:
        raise ResearchError("INVALID_LISTING_URL", "L’URL de l’annonce est invalide.")
    try:
        records = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ResearchError(
            "LISTING_HOST_UNREACHABLE",
            "Le nom de domaine de l’annonce est introuvable.",
            retryable=True,
        ) from exc
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise ResearchError(
                "UNSAFE_LISTING_URL",
                "L’annonce redirige vers une adresse locale ou privée.",
            )


class ListingHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.images: list[str] = []
        self.json_scripts: list[str] = []
        self._json_buffer: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if key and content:
                self.meta[key] = content
                if key in {"og:image", "twitter:image", "twitter:image:src"}:
                    self.images.append(content)
        elif tag == "img":
            for key in ("src", "data-src", "data-lazy-src"):
                if values.get(key):
                    self.images.append(values[key])
            srcset = values.get("srcset") or values.get("data-srcset")
            if srcset:
                candidates = [part.strip().split()[0] for part in srcset.split(",") if part.strip()]
                if candidates:
                    self.images.append(candidates[-1])
        elif tag == "script":
            script_type = values.get("type", "").lower()
            script_id = values.get("id", "").lower()
            if "json" in script_type or script_id == "__next_data__":
                self._json_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "script" and self._json_buffer is not None:
            body = "".join(self._json_buffer).strip()
            if body:
                self.json_scripts.append(body)
            self._json_buffer = None

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self._json_buffer is not None:
            self._json_buffer.append(data)


def _walk_json(value: Any, images: list[str], locations: list[str], coordinates: list[tuple[float, float]]) -> None:
    if isinstance(value, dict):
        lower = {str(key).lower(): item for key, item in value.items()}
        for key in ("image", "images", "imageurl", "image_url", "urls", "pictures"):
            if key in lower:
                candidate = lower[key]
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://", "/")):
                    images.append(candidate)
                elif isinstance(candidate, list):
                    for item in candidate:
                        if isinstance(item, str):
                            images.append(item)
        for key in ("city", "zipcode", "postalcode", "postal_code", "location", "city_label"):
            candidate = lower.get(key)
            if isinstance(candidate, str) and candidate.strip():
                locations.append(candidate.strip())
        lat = lower.get("latitude", lower.get("lat"))
        lon = lower.get("longitude", lower.get("lng", lower.get("lon")))
        try:
            if lat is None or lon is None:
                raise ValueError("missing coordinates")
            latitude, longitude = float(lat), float(lon)
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                coordinates.append((latitude, longitude))
        except (TypeError, ValueError):
            pass
        for item in value.values():
            _walk_json(item, images, locations, coordinates)
    elif isinstance(value, list):
        for item in value:
            _walk_json(item, images, locations, coordinates)


class ListingFetcher:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        syndication_discoverer: SyndicationDiscoverer | None = None,
    ):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; Netryx-Web/1.0)"})
        self.syndication_discoverer = syndication_discoverer or default_discoverer()

    def _direct_html(self, listing_url: str) -> str:
        current = listing_url
        for _ in range(6):
            _assert_public_dns(current)
            response = self.session.get(current, timeout=35, allow_redirects=False, stream=True)
            if response.status_code in {301, 302, 303, 307, 308}:
                target = response.headers.get("location")
                response.close()
                if not target:
                    break
                try:
                    current = validate_listing_url(urljoin(current, target))
                except UnsafeUrlError as exc:
                    raise ResearchError("UNSAFE_LISTING_REDIRECT", str(exc)) from exc
                continue
            if response.status_code in {401, 403, 429}:
                response.close()
                raise ResearchError(
                    "LISTING_ACCESS_BLOCKED",
                    "Le site de l’annonce bloque l’accès automatisé aux photos.",
                    retryable=True,
                )
            try:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    body.extend(chunk)
                    if len(body) > HTML_LIMIT:
                        raise ResearchError(
                            "LISTING_TOO_LARGE",
                            "La page de l’annonce dépasse la taille autorisée.",
                        )
                encoding = response.encoding or "utf-8"
                return bytes(body).decode(encoding, errors="replace")
            finally:
                response.close()
        raise ResearchError("TOO_MANY_REDIRECTS", "L’annonce effectue trop de redirections.")

    def _scrapfly_html(self, listing_url: str, api_key: str) -> str:
        try:
            response = self.session.get(
                "https://api.scrapfly.io/scrape",
                params={
                    "key": api_key,
                    "url": listing_url,
                    "asp": "true",
                    "country": "fr",
                    "render_js": "true",
                },
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload.get("result", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("missing result.content")
            return content
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise ResearchError(
                "LISTING_PROXY_FAILED",
                "Le service autorisé de récupération de l’annonce a échoué.",
                retryable=True,
            ) from exc

    def _download_images(self, urls: list[str], base_url: str, work_dir: Path) -> list[dict[str, Any]]:
        image_dir = work_dir / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        for candidate in urls:
            if len(artifacts) >= MAX_IMAGES:
                break
            absolute = urljoin(base_url, candidate)
            try:
                absolute = validate_listing_url(absolute)
            except UnsafeUrlError:
                continue
            if absolute in seen_urls:
                continue
            seen_urls.add(absolute)
            try:
                _assert_public_dns(absolute)
                response = self.session.get(absolute, timeout=35, stream=True, allow_redirects=True)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if not content_type.startswith("image/"):
                    response.close()
                    continue
                data = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    data.extend(chunk)
                    if len(data) > IMAGE_LIMIT:
                        raise ValueError("image too large")
                response.close()
            except (requests.RequestException, ValueError, ResearchError):
                continue
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            extension = mimetypes.guess_extension(content_type) or ".jpg"
            path = image_dir / f"{len(artifacts) + 1:02d}-{digest[:12]}{extension}"
            path.write_bytes(data)
            artifacts.append(
                {
                    "path": str(path.resolve()),
                    "source_url": absolute,
                    "sha256": digest,
                    "content_type": content_type,
                    "bytes": len(data),
                }
            )
        return artifacts

    def fetch(self, listing_url: str, work_dir: str | Path) -> dict[str, Any]:
        listing_url = validate_listing_url(listing_url)
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            html = self._direct_html(listing_url)
            acquisition = "direct"
        except ResearchError as exc:
            key = os.environ.get("SCRAPFLY_API_KEY", "").strip()
            if exc.code != "LISTING_ACCESS_BLOCKED" or not key:
                if exc.code == "LISTING_ACCESS_BLOCKED":
                    listing_id = listing_url.rstrip("/").rsplit("/", 1)[-1] or None
                    try:
                        report = self.syndication_discoverer.discover(
                            SyndicationRequest(submitted_url=listing_url, listing_id=listing_id)
                        )
                    except Exception:
                        report = error_report("SYNDICATION_PROVIDER_FAILED")
                    (work_dir / "syndication-report.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                raise
            html = self._scrapfly_html(listing_url, key)
            acquisition = "scrapfly"

        parser = ListingHtmlParser()
        parser.feed(html)
        images = list(parser.images)
        locations: list[str] = []
        coordinates: list[tuple[float, float]] = []
        for script in parser.json_scripts:
            try:
                data = json.loads(script)
            except json.JSONDecodeError:
                continue
            _walk_json(data, images, locations, coordinates)

        artifacts = self._download_images(images, listing_url, work_dir)
        if not artifacts:
            raise ResearchError(
                "NO_LISTING_IMAGES",
                "Aucune photo exploitable n’a été trouvée dans cette annonce.",
            )
        title = parser.meta.get("og:title") or " ".join(parser.title_parts).strip() or None
        description = parser.meta.get("og:description") or parser.meta.get("description") or None
        center = coordinates[0] if coordinates else None
        location_hints = list(dict.fromkeys(locations))
        manifest = {
            "schema_version": 2,
            "source_role": "primary_listing",
            "submitted_url": listing_url,
            "source_url": listing_url,
            "acquisition": acquisition,
            "title": title,
            "description": description,
            "location_hints": location_hints,
            "location_hint": ", ".join(location_hints[:4]) or None,
            "center": {"latitude": center[0], "longitude": center[1]} if center else None,
            "images": artifacts,
        }
        report = self.syndication_discoverer.discover(request_from_manifest(manifest))
        manifest["syndication"] = report
        for candidate in report.get("candidates") or []:
            hint = candidate.get("location_hint") if isinstance(candidate, dict) else None
            if isinstance(hint, str) and hint.strip() and hint not in location_hints:
                location_hints.append(hint)
        manifest["location_hints"] = location_hints
        return manifest
