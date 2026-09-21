import json
import shlex
import sys

from netryx_web.listing import ListingFetcher
from netryx_web.research import ResearchError
from netryx_web.syndication import (
    CommandSyndicationDiscoverer,
    FirecrawlSyndicationDiscoverer,
    SyndicationRequest,
    normalize_report,
    unavailable_report,
)


def test_missing_search_provider_is_unavailable_not_not_found():
    report = CommandSyndicationDiscoverer(command="").discover(
        SyndicationRequest(submitted_url="https://www.leboncoin.fr/ad/ventes_immobilieres/123")
    )

    assert report["status"] == "unavailable"
    assert report["error_code"] == "SYNDICATION_PROVIDER_NOT_CONFIGURED"


def test_provider_failure_cannot_be_serialized_as_not_found():
    report = normalize_report(
        {
            "status": "not_found",
            "error_code": "RATE_LIMITED",
        },
        provider="test",
    )

    assert report["status"] == "error"
    assert report["error_code"] == "RATE_LIMITED"


def test_found_report_preserves_mirror_provenance():
    report = normalize_report(
        {
            "status": "found",
            "queries": ["listing 123"],
            "candidates": [
                {
                    "url": "https://example.org/house/123",
                    "title": "Maison",
                    "location_hint": "Nearby Town 00001",
                    "match_basis": ["price", "surface", "photos"],
                }
            ],
        },
        provider="test",
    )

    assert report["status"] == "found"
    assert report["candidates"] == [
        {
            "result_url": "https://example.org/house/123",
            "host": "example.org",
            "title": "Maison",
            "location_hint": "Nearby Town 00001",
            "match_basis": ["price", "surface", "photos"],
            "provider": "test",
            "query": "listing 123",
        }
    ]


def test_listing_manifest_runs_syndication_check_and_keeps_primary_source(tmp_path):
    class FakeDiscoverer:
        def __init__(self):
            self.requests = []

        def discover(self, request):
            self.requests.append(request)
            return {
                "status": "found",
                "provider": "fake",
                "checked_at": "2026-09-21T00:00:00+00:00",
                "queries": ["listing 123"],
                "candidates": [
                    {
                        "result_url": "https://mirror.example/123",
                        "host": "mirror.example",
                        "location_hint": "Nearby Town 00001",
                        "match_basis": ["photos"],
                    }
                ],
            }

    discoverer = FakeDiscoverer()
    fetcher = ListingFetcher(syndication_discoverer=discoverer)
    fetcher._direct_html = lambda _: '<meta property="og:title" content="Maison">'
    fetcher._download_images = lambda *_args: [
        {
            "path": str(tmp_path / "01.jpg"),
            "source_url": "https://www.leboncoin.fr/image.jpg",
            "sha256": "abc",
            "content_type": "image/jpeg",
            "bytes": 3,
        }
    ]

    manifest = fetcher.fetch(
        "https://www.leboncoin.fr/ad/ventes_immobilieres/123",
        tmp_path,
    )

    assert manifest["source_role"] == "primary_listing"
    assert manifest["source_url"] == "https://www.leboncoin.fr/ad/ventes_immobilieres/123"
    assert manifest["syndication"]["status"] == "found"
    assert manifest["syndication"]["candidates"][0]["result_url"] == "https://mirror.example/123"
    assert "Nearby Town 00001" in manifest["location_hints"]
    assert discoverer.requests[0].submitted_url == manifest["source_url"]


def test_command_discoverer_accepts_json_report():
    payload = {
        "status": "not_found",
        "queries": ["listing 123"],
        "candidates": [],
    }
    script = f"import json; print(json.dumps({payload!r}))"
    command = f"{sys.executable} -c {shlex.quote(script)}"

    report = CommandSyndicationDiscoverer(command=command).discover(
        SyndicationRequest(submitted_url="https://www.leboncoin.fr/ad/ventes_immobilieres/123")
    )

    assert report["status"] == "not_found"
    assert report["provider"] == "command"


def test_firecrawl_adapter_accepts_only_multi_signal_mirror_candidates():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "web": [
                        {
                            "url": "https://mirror.example/house/123",
                            "title": "Maison 82 m2 5 pieces",
                            "description": "Maison a Lacroix Saint Ouen 60610, jardin clos",
                        },
                        {
                            "url": "https://unrelated.example/house",
                            "title": "Maison a vendre",
                            "description": "Paris",
                        },
                    ]
                }
            }

    class Session:
        def post(self, *_args, **_kwargs):
            return Response()

    report = FirecrawlSyndicationDiscoverer(session=Session()).discover(
        SyndicationRequest(
            submitted_url="https://www.leboncoin.fr/ad/ventes_immobilieres/123",
            listing_id="123",
            title="Maison 82 m² 5 pièces",
            location_hints=["Lacroix Saint Ouen 60610"],
        )
    )

    assert report["status"] == "found"
    assert [item["result_url"] for item in report["candidates"]] == [
        "https://mirror.example/house/123"
    ]
    assert set(report["candidates"][0]["match_basis"]) >= {"title", "location"}


def test_blocked_primary_still_writes_syndication_report(tmp_path, monkeypatch):
    class Discoverer:
        def discover(self, request):
            return {
                "status": "not_found",
                "provider": "test",
                "checked_at": "2026-09-21T00:00:00+00:00",
                "queries": [request.listing_id or ""],
                "candidates": [],
            }

    def blocked(_url):
        raise ResearchError("LISTING_ACCESS_BLOCKED", "blocked")

    monkeypatch.delenv("SCRAPFLY_API_KEY", raising=False)
    fetcher = ListingFetcher(syndication_discoverer=Discoverer())
    fetcher._direct_html = blocked

    try:
        fetcher.fetch("https://www.leboncoin.fr/ad/ventes_immobilieres/123", tmp_path)
    except ResearchError as exc:
        assert exc.code == "LISTING_ACCESS_BLOCKED"
    else:
        raise AssertionError("blocked acquisition unexpectedly succeeded")

    report = json.loads((tmp_path / "syndication-report.json").read_text())
    assert report["status"] == "not_found"
