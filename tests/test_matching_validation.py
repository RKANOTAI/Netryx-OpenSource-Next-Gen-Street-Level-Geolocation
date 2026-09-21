import sys

import numpy as np

from netryx_web.live_runner import _ransac_inliers, aggregate_verified_candidates


class FakeCV2:
    RANSAC = object()

    class error(Exception):
        pass


def _matches(count: int) -> np.ndarray:
    return np.arange(count, dtype=np.int64)


def test_ransac_fewer_than_min_matches_has_no_geometric_inliers(monkeypatch):
    fake = FakeCV2()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("RANSAC must not run below its minimum match count")

    fake.findHomography = fail_if_called
    monkeypatch.setitem(sys.modules, "cv2", fake)

    raw, inliers = _ransac_inliers(
        np.zeros((5, 2)), np.zeros((5, 2)), _matches(5)
    )

    assert raw == 5
    assert inliers == 0


def test_ransac_without_matrix_or_mask_has_no_geometric_inliers(monkeypatch):
    fake = FakeCV2()
    fake.findHomography = lambda *_args, **_kwargs: (None, None)
    monkeypatch.setitem(sys.modules, "cv2", fake)

    raw, inliers = _ransac_inliers(
        np.zeros((8, 2)), np.zeros((8, 2)), _matches(8)
    )

    assert raw == 8
    assert inliers == 0


def test_ransac_exception_has_no_geometric_inliers(monkeypatch):
    fake = FakeCV2()

    def fail(*_args, **_kwargs):
        raise fake.error("synthetic RANSAC failure")

    fake.findHomography = fail
    monkeypatch.setitem(sys.modules, "cv2", fake)

    raw, inliers = _ransac_inliers(
        np.zeros((8, 2)), np.zeros((8, 2)), _matches(8)
    )

    assert raw == 8
    assert inliers == 0


def test_aggregate_counts_only_rows_meeting_inlier_threshold():
    ranked = aggregate_verified_candidates(
        [
            {
                "panoid": "one",
                "lat": 1.0,
                "lon": 2.0,
                "image": "weak.jpg",
                "heading": 90,
                "inliers": 7,
                "raw_matches": 30,
            },
            {
                "panoid": "one",
                "lat": 1.0,
                "lon": 2.0,
                "image": "strong.jpg",
                "heading": 105,
                "inliers": 8,
                "raw_matches": 12,
            },
        ]
    )

    assert ranked[0]["query_support"] == 1
    assert ranked[0]["sum_inliers"] == 8
    assert [source["image"] for source in ranked[0]["sources"]] == ["strong.jpg"]


def test_aggregate_deduplicates_pano_and_image_using_best_view():
    ranked = aggregate_verified_candidates(
        [
            {
                "panoid": "one",
                "lat": 1.0,
                "lon": 2.0,
                "image": "a.jpg",
                "heading": 90,
                "inliers": 10,
                "raw_matches": 12,
            },
            {
                "panoid": "one",
                "lat": 9.0,
                "lon": 9.0,
                "image": "a.jpg",
                "heading": 120,
                "inliers": 20,
                "raw_matches": 22,
            },
            {
                "panoid": "one",
                "lat": 1.0,
                "lon": 2.0,
                "image": "b.jpg",
                "heading": 135,
                "inliers": 8,
                "raw_matches": 9,
            },
        ]
    )

    assert len(ranked) == 1
    candidate = ranked[0]
    assert candidate["query_support"] == 2
    assert candidate["sum_inliers"] == 28
    assert candidate["best_inliers"] == 20
    assert candidate["best_raw_matches"] == 22
    assert candidate["lat"] == 9.0
    assert candidate["lon"] == 9.0
    assert [(source["image"], source["inliers"]) for source in candidate["sources"]] == [
        ("a.jpg", 20),
        ("b.jpg", 8),
    ]
