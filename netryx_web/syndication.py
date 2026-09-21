from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
import unicodedata
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests


VALID_STATUSES = frozenset({"found", "not_found", "unavailable", "error"})


@dataclass(frozen=True)
class SyndicationRequest:
    submitted_url: str
    listing_id: str | None = None
    title: str | None = None
    description: str | None = None
    location_hints: list[str] = field(default_factory=list)
    image_sha256: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MirrorCandidate:
    result_url: str
    host: str
    title: str | None = None
    location_hint: str | None = None
    match_basis: list[str] = field(default_factory=list)
    provider: str | None = None
    query: str | None = None


class SyndicationDiscoverer(Protocol):
    def discover(self, request: SyndicationRequest) -> dict[str, Any]: ...


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def unavailable_report(reason: str, *, provider: str | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "provider": provider,
        "checked_at": _checked_at(),
        "queries": [],
        "candidates": [],
        "error_code": reason,
    }


def error_report(reason: str, *, provider: str | None = None) -> dict[str, Any]:
    return {
        "status": "error",
        "provider": provider,
        "checked_at": _checked_at(),
        "queries": [],
        "candidates": [],
        "error_code": reason,
    }


def _candidate(value: Any, *, provider: str | None, query: str | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result_url = value.get("result_url") or value.get("url")
    if not isinstance(result_url, str) or not urlsplit(result_url).hostname:
        return None
    host = urlsplit(result_url).netloc.lower()
    basis = value.get("match_basis") or []
    if isinstance(basis, str):
        basis = [basis]
    if not isinstance(basis, list):
        basis = []
    return asdict(
        MirrorCandidate(
            result_url=result_url,
            host=host,
            title=value.get("title") if isinstance(value.get("title"), str) else None,
            location_hint=(
                value.get("location_hint") if isinstance(value.get("location_hint"), str) else None
            ),
            match_basis=[item for item in basis if isinstance(item, str)],
            provider=value.get("provider") if isinstance(value.get("provider"), str) else provider,
            query=value.get("query") if isinstance(value.get("query"), str) else query,
        )
    )


def normalize_report(payload: Any, *, provider: str) -> dict[str, Any]:
    """Validate provider output without turning provider failures into no-result."""
    if not isinstance(payload, dict):
        return error_report("SYNDICATION_INVALID_RESPONSE", provider=provider)
    status = payload.get("status")
    if status not in VALID_STATUSES:
        return error_report("SYNDICATION_INVALID_STATUS", provider=provider)
    queries = payload.get("queries") or []
    if isinstance(queries, str):
        queries = [queries]
    if not isinstance(queries, list):
        queries = []
    candidates = [
        item
        for item in (
            _candidate(value, provider=provider, query=queries[0] if queries else None)
            for value in (payload.get("candidates") or [])
        )
        if item is not None
    ]
    if status == "found" and not candidates:
        return error_report("SYNDICATION_FOUND_WITHOUT_CANDIDATES", provider=provider)
    if status == "not_found" and payload.get("error_code"):
        return error_report(str(payload["error_code"]), provider=provider)
    if status == "not_found" and not payload.get("checked_at"):
        checked_at = _checked_at()
    else:
        checked_at = payload.get("checked_at") or _checked_at()
    return {
        "status": status,
        "provider": payload.get("provider") or provider,
        "checked_at": checked_at,
        "queries": [item for item in queries if isinstance(item, str)],
        "candidates": candidates,
        "error_code": payload.get("error_code"),
    }


class CommandSyndicationDiscoverer:
    """Adapter for a separately managed, terms-compliant search provider.

    The command receives a JSON request on stdin and must return the report JSON
    documented by ``normalize_report``. It is intentionally not a mirror fetcher.
    """

    def __init__(self, command: str | None = None, *, timeout_s: float | None = None):
        self.command = command if command is not None else os.environ.get("NETRYX_SYNDICATION_COMMAND", "")
        self.timeout_s = timeout_s or float(os.environ.get("NETRYX_SYNDICATION_TIMEOUT_S", "15"))

    def discover(self, request: SyndicationRequest) -> dict[str, Any]:
        if not self.command.strip():
            return unavailable_report("SYNDICATION_PROVIDER_NOT_CONFIGURED")
        try:
            completed = subprocess.run(
                shlex.split(self.command),
                input=json.dumps(asdict(request), ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return error_report("SYNDICATION_COMMAND_FAILED", provider="command")
        if completed.returncode != 0:
            return error_report("SYNDICATION_COMMAND_FAILED", provider="command")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return error_report("SYNDICATION_INVALID_RESPONSE", provider="command")
        return normalize_report(payload, provider="command")


def _search_tokens(value: str | None) -> list[str]:
    folded = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return [token for token in re.findall(r"[a-z0-9-]+", folded.lower()) if len(token) >= 4]


class FirecrawlSyndicationDiscoverer:
    """Search adapter using Firecrawl's documented web-search endpoint."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        api_key: str | None = None,
        max_results: int = 8,
        timeout_s: float | None = None,
    ):
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else os.environ.get("FIRECRAWL_API_KEY", "").strip()
        self.max_results = max(1, min(max_results, 20))
        self.timeout_s = timeout_s or float(os.environ.get("NETRYX_SYNDICATION_TIMEOUT_S", "15"))

    @staticmethod
    def _queries(request: SyndicationRequest) -> list[str]:
        queries: list[str] = []
        if request.listing_id:
            queries.append(f'"{request.listing_id}" immobilier')
        title = " ".join(_search_tokens(request.title)[:12])
        location = " ".join(_search_tokens(" ".join(request.location_hints))[:8])
        if title and location:
            queries.append(f'"{title}" {location}')
        elif title:
            queries.append(f'"{title}" immobilier')
        description_tokens = " ".join(_search_tokens(request.description)[:10])
        if description_tokens and description_tokens != title:
            queries.append(f'"{description_tokens}" immobilier')
        return list(dict.fromkeys(queries))[:3]

    def discover(self, request: SyndicationRequest) -> dict[str, Any]:
        queries = self._queries(request)
        if not queries:
            return unavailable_report("SYNDICATION_INSUFFICIENT_SEARCH_SIGNATURE", provider="firecrawl")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        rows: dict[str, dict[str, Any]] = {}
        try:
            for query in queries:
                response = self.session.post(
                    "https://api.firecrawl.dev/v2/search",
                    json={"query": query, "limit": self.max_results, "sources": ["web"]},
                    headers=headers,
                    timeout=self.timeout_s,
                )
                response.raise_for_status()
                payload = response.json()
                for row in (payload.get("data") or {}).get("web") or []:
                    if not isinstance(row, dict) or not isinstance(row.get("url"), str):
                        continue
                    url = row["url"]
                    host = (urlsplit(url).hostname or "").lower()
                    if not host or host == "leboncoin.fr" or host.endswith(".leboncoin.fr"):
                        continue
                    text = " ".join(
                        str(row.get(key) or "") for key in ("title", "description", "snippet")
                    ).lower()
                    basis: list[str] = []
                    if request.listing_id and request.listing_id.lower() in text:
                        basis.append("listing_id")
                    title_tokens = set(_search_tokens(request.title))
                    if title_tokens and len(title_tokens.intersection(_search_tokens(text))) >= min(3, len(title_tokens)):
                        basis.append("title")
                    location_tokens = set(_search_tokens(" ".join(request.location_hints)))
                    if location_tokens and len(location_tokens.intersection(_search_tokens(text))) >= min(2, len(location_tokens)):
                        basis.append("location")
                    if len(basis) < 2 and "listing_id" not in basis:
                        continue
                    postal_match = re.search(r"\b\d{5}\b", text)
                    rows.setdefault(
                        url,
                        {
                            "result_url": url,
                            "title": row.get("title"),
                            "location_hint": postal_match.group(0) if postal_match else None,
                            "match_basis": basis,
                            "provider": "firecrawl",
                            "query": query,
                        },
                    )
        except (requests.RequestException, ValueError, TypeError, KeyError):
            return error_report("SYNDICATION_PROVIDER_FAILED", provider="firecrawl")
        if not rows:
            return normalize_report(
                {"status": "not_found", "queries": queries, "candidates": []},
                provider="firecrawl",
            )
        return normalize_report(
            {"status": "found", "queries": queries, "candidates": list(rows.values())},
            provider="firecrawl",
        )


def default_discoverer() -> SyndicationDiscoverer:
    provider = os.environ.get("NETRYX_SYNDICATION_PROVIDER", "command").strip().lower()
    if provider == "firecrawl":
        return FirecrawlSyndicationDiscoverer()
    return CommandSyndicationDiscoverer()


def request_from_manifest(manifest: dict[str, Any]) -> SyndicationRequest:
    submitted_url = str(manifest.get("submitted_url") or manifest.get("source_url") or "")
    listing_id = submitted_url.rstrip("/").rsplit("/", 1)[-1] or None
    locations = [item for item in manifest.get("location_hints") or [] if isinstance(item, str)]
    location_hint = manifest.get("location_hint")
    if isinstance(location_hint, str) and location_hint not in locations:
        locations.append(location_hint)
    hashes = [
        str(item["sha256"])
        for item in manifest.get("images") or []
        if isinstance(item, dict) and item.get("sha256")
    ]
    return SyndicationRequest(
        submitted_url=submitted_url,
        listing_id=listing_id,
        title=manifest.get("title") if isinstance(manifest.get("title"), str) else None,
        description=(manifest.get("description") if isinstance(manifest.get("description"), str) else None),
        location_hints=locations,
        image_sha256=hashes,
    )
