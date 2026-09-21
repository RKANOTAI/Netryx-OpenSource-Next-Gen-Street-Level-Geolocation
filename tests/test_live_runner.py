import math

import pytest
from PIL import Image

from netryx_web.live_runner import (
    aggregate_verified_candidates,
    build_result,
    build_static_view_url,
    grid_points,
    query_images,
    _manifest_centers,
)
from netryx_web.photo_selection import ExteriorDecision, select_exterior_images


def test_grid_points_are_derived_from_requested_center_and_radius():
    center = (49.1234, 2.4567)
    points = grid_points(center, radius_m=100, grid_size=5)

    assert len(points) == 25
    assert points[12] == center
    assert any(point != center for point in points)
    assert all(abs(point[0] - center[0]) < 0.01 for point in points)
    assert all(abs(point[1] - center[1]) < 0.01 for point in points)


def test_query_images_keeps_manifest_paths_without_hardcoded_filenames(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.png"
    Image.new("RGB", (80, 80), "red").save(first)
    Image.new("RGB", (80, 80), "blue").save(second)

    manifest = {
        "images": [
            {"path": str(first), "sha256": "a"},
            {"path": str(second), "sha256": "b"},
        ]
    }

    classifier = lambda path: ExteriorDecision(
        exterior_score=0.9,
        interior_score=0.0,
        decision="ACCEPT_EXTERIOR",
        reason="test exterior",
    )

    assert query_images(manifest, limit=2, classifier=classifier) == [first, second]


def test_exterior_selection_skips_interiors_before_accepting_exteriors(tmp_path):
    interior = tmp_path / "interior.jpg"
    exterior_one = tmp_path / "front.jpg"
    exterior_two = tmp_path / "street.jpg"
    for path, color in ((interior, "white"), (exterior_one, "red"), (exterior_two, "blue")):
        Image.new("RGB", (80, 80), color).save(path)

    scores = {
        interior: ExteriorDecision(0.8, 0.8, "REJECT_INTERIOR", "indoor veto"),
        exterior_one: ExteriorDecision(0.85, 0.1, "ACCEPT_EXTERIOR", "strong exterior"),
        exterior_two: ExteriorDecision(0.8, 0.1, "ACCEPT_EXTERIOR", "strong exterior"),
    }
    selected, audit = select_exterior_images(
        {"images": [{"path": str(path)} for path in scores]},
        limit=2,
        classifier=lambda path: scores[path],
    )

    assert selected == [exterior_one, exterior_two]
    assert [item["decision"] for item in audit] == [
        "REJECT_INTERIOR",
        "ACCEPT_EXTERIOR",
        "ACCEPT_EXTERIOR",
    ]


def test_exterior_selection_does_not_use_uncertain_images_to_fill_limit(tmp_path):
    uncertain = tmp_path / "uncertain.jpg"
    exterior = tmp_path / "exterior.jpg"
    Image.new("RGB", (80, 80), "white").save(uncertain)
    Image.new("RGB", (80, 80), "black").save(exterior)

    selected, audit = select_exterior_images(
        {"images": [{"path": str(uncertain)}, {"path": str(exterior)}]},
        limit=2,
        classifier=lambda path: ExteriorDecision(
            0.4 if path == uncertain else 0.9,
            0.2 if path == uncertain else 0.1,
            "REJECT_UNCERTAIN" if path == uncertain else "ACCEPT_EXTERIOR",
            "low confidence" if path == uncertain else "strong exterior",
        ),
    )

    assert selected == [exterior]
    assert audit[0]["decision"] == "REJECT_UNCERTAIN"


def test_exterior_selection_fails_closed_when_no_photo_is_confident(tmp_path):
    image = tmp_path / "unknown.jpg"
    Image.new("RGB", (80, 80), "gray").save(image)

    selected, audit = select_exterior_images(
        {"images": [{"path": str(image)}]},
        limit=2,
        classifier=lambda _: ExteriorDecision(0.3, 0.2, "REJECT_UNCERTAIN", "low margin"),
    )

    assert selected == []
    assert audit[0]["decision"] == "REJECT_UNCERTAIN"


def test_manifest_centers_include_primary_and_syndicated_location_hints(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            hint = self.hint
            coordinates = {
                "Displayed Town 00000": [2.0, 49.0],
                "Nearby Town 00001": [2.02, 49.02],
            }[hint]
            return {"features": [{"geometry": {"coordinates": coordinates}}]}

    def fake_get(_url, *, params, **_kwargs):
        response = Response()
        response.hint = params["q"]
        return response

    monkeypatch.setattr("requests.get", fake_get)
    centers = _manifest_centers(
        {
            "location_hint": "Displayed Town 00000",
            "syndication": {
                "candidates": [{"location_hint": "Nearby Town 00001"}],
            },
        }
    )

    assert centers == [(49.0, 2.0), (49.02, 2.02)]


def test_aggregate_verified_candidates_rewards_multi_image_support():
    rows = [
        {"panoid": "one", "lat": 1.0, "lon": 2.0, "image": "a", "heading": 90, "inliers": 40, "raw_matches": 50},
        {"panoid": "one", "lat": 1.0, "lon": 2.0, "image": "b", "heading": 105, "inliers": 35, "raw_matches": 45},
        {"panoid": "two", "lat": 3.0, "lon": 4.0, "image": "a", "heading": 180, "inliers": 60, "raw_matches": 70},
    ]

    ranked = aggregate_verified_candidates(rows)

    assert ranked[0]["panoid"] == "one"
    assert ranked[0]["query_support"] == 2
    assert ranked[0]["sum_inliers"] == 75


def test_static_view_url_is_derived_from_pano_and_heading():
    url = build_static_view_url("dynamic-pano", 135, "not-printed-test-key")

    assert "pano=dynamic-pano" in url
    assert "heading=135.000" in url
    assert "key=not-printed-test-key" in url


def test_build_result_uses_candidate_coordinates_and_generates_map_url():
    candidate = {
        "panoid": "dynamic-pano",
        "lat": 48.111111,
        "lon": 2.222222,
        "query_support": 2,
        "sum_inliers": 75,
        "best_inliers": 40,
        "sources": [{"image": "first.jpg", "heading": 90, "inliers": 40}],
    }

    result = build_result(
        candidate,
        panos_indexed=12,
        views_tested=36,
        reverse_geocode=lambda lat, lon: "Zone test",
    )

    assert result["location"]["latitude"] == pytest.approx(48.111111)
    assert result["location"]["longitude"] == pytest.approx(2.222222)
    assert "48.1111110" in result["google_maps_url"]
    assert "2.2222220" in result["google_maps_url"]
    assert result["location"]["label_fr"] == "Zone test"
    assert result["evidence"][1]["value"] == 12
