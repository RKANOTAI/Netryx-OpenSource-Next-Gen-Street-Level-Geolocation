import json
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from netryx_web import live_runner


def test_default_provider_does_not_require_google_key(tmp_path, monkeypatch):
    monkeypatch.delenv("NETRYX_IMAGERY_PROVIDER", raising=False)
    monkeypatch.delenv("GOOGLE_STREETVIEW_API_KEY", raising=False)
    expected = [{"panoid": "open-image", "lat": 48.5, "lon": 2.5}]
    fake = SimpleNamespace(discover=lambda centers, radius, work: expected)
    monkeypatch.setitem(sys.modules, "netryx_web.panoramax", fake)
    assert live_runner.crawl_panos_many([(48.5, 2.5)], 100, tmp_path) == expected


def test_google_remains_explicit_and_authorization_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("NETRYX_IMAGERY_PROVIDER", "google")
    monkeypatch.delenv("GOOGLE_STREETVIEW_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_STREETVIEW_AUTH_REQUIRED"):
        live_runner.crawl_panos_many([(48.5, 2.5)], 100, tmp_path)


def test_unknown_provider_does_not_silently_use_google(tmp_path, monkeypatch):
    monkeypatch.setenv("NETRYX_IMAGERY_PROVIDER", "typo")
    with pytest.raises(ValueError, match="NETRYX_IMAGERY_PROVIDER"):
        live_runner.crawl_panos_many([(48.5, 2.5)], 100, tmp_path)


def test_index_routes_to_open_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("NETRYX_IMAGERY_PROVIDER", raising=False)
    expected = ("descriptors", "metadata", 1)
    monkeypatch.setitem(sys.modules, "netryx_web.panoramax", SimpleNamespace(
        build_index=lambda panos, work, headings: expected,
    ))
    assert live_runner._build_global_index([], tmp_path, [0, 90]) == expected


def test_global_top_k_counts_distinct_panoramas(tmp_path, monkeypatch):
    image = tmp_path / "query.jpg"
    Image.new("RGB", (100, 100)).save(image)
    monkeypatch.setitem(sys.modules, "cosplace_utils", SimpleNamespace(
        extract_cosplace_descriptor=lambda picture: np.array([1.0]),
    ))
    metadata = [{"panoid": pano, "heading": heading} for pano, heading in [
        ("same", 0), ("same", 45), ("same", 90), ("other", 0),
    ]]
    rows = live_runner._global_search([image], np.array([[.9], [.8], [.7], [.6]]), metadata, 2)
    assert {row["panoid"] for row in rows} == {"same", "other"}


def test_run_honors_region_threshold_and_records_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("NETRYX_MIN_INLIERS", "20")
    monkeypatch.setenv("NETRYX_IMAGERY_PROVIDER", "panoramax")
    manifest_path = tmp_path / "listing-manifest.json"
    manifest_path.write_text(json.dumps({
        "center": {"latitude": 48.5, "longitude": 2.5}, "search_radius_m": 125,
    }))
    monkeypatch.setattr(live_runner, "query_images", lambda *a, **k: [tmp_path / "photo.jpg"])
    captured = {}
    pano = {"panoid": "one", "lat": 48.5, "lon": 2.5, "provider": "panoramax",
            "source_url": "https://panoramax.ign.fr/example.jpg", "license": "etalab-2.0",
            "license_url": "https://example.org/license", "attribution": ["Test producer"]}

    def crawl(centers, radius, work_dir):
        captured["radius"] = radius
        return [pano]

    monkeypatch.setattr(live_runner, "crawl_panos_many", crawl)
    monkeypatch.setattr(live_runner, "_build_global_index", lambda *a: ([], [pano], 1))
    monkeypatch.setattr(live_runner, "_global_search", lambda *a: [])
    monkeypatch.setattr(live_runner, "_verify", lambda *a: [
        {**pano, "image": "photo.jpg", "heading": 0, "inliers": 30, "raw_matches": 40},
        {**pano, "image": "weak.jpg", "heading": 0, "inliers": 9, "raw_matches": 40},
    ])
    actual_build = live_runner.build_result
    monkeypatch.setattr(live_runner, "build_result", lambda *a, **k: actual_build(
        *a, **k, reverse_geocode=lambda *_: "Test area",
    ))
    result = live_runner._run(manifest_path)
    assert captured["radius"] == 125
    assert result["confidence"]["level"] == "MEDIUM"
    audit = json.loads((tmp_path / "search-evidence.json").read_text())
    assert audit["ranked"][0]["query_support"] == 1
    assert audit["provider"] == "panoramax"
    assert any("etalab-2.0" in item["detail_fr"] and "Test producer" in item["detail_fr"]
               for item in result["evidence"])
    assert "Street View" not in result["summary_fr"]
