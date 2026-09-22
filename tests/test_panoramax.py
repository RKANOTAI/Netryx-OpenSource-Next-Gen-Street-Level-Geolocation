import json
from pathlib import Path

import pytest

from netryx_web import panoramax


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _feature(panoid, lon, lat, *, license_name="etalab-2.0", href=None, azimuth=3):
    return {
        "id": panoid,
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "license": license_name,
            "license_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
            "geovisio:producer": "test producer",
            "datetime": "2024-01-02T03:04:05Z",
            "view:azimuth": azimuth,
            "field_of_view": 360,
        },
        "assets": {
            "sd": {"href": href or f"https://panoramax.ign.fr/api/pictures/{panoid}/sd.jpg"}
        },
    }


def test_discover_deduplicates_filters_circle_and_writes_audit(tmp_path, monkeypatch):
    inside = "c59ab61f-0a62-4f27-8724-017df8c2730a"
    outside = "04b7e9cc-76d0-4d42-85f0-1b2f7c8da1e3"
    forbidden = "c0a8012e-5c47-49a7-8c63-0a0a2e5df001"
    payload = {
        "type": "FeatureCollection",
        "features": [
            _feature(inside, 2.0005, 49.0005),
            _feature(inside, 2.0005, 49.0005),
            _feature(outside, 2.01, 49.0),
            _feature(forbidden, 2.0002, 49.0002, license_name="CC-BY-4.0"),
        ],
        "links": [],
    }
    calls = []

    def fake_get(url, *, params, **kwargs):
        calls.append((url, params, kwargs))
        return FakeResponse(payload)

    monkeypatch.setattr("requests.get", fake_get)

    panos = panoramax.discover([(49.0, 2.0), (49.0001, 2.0001)], 100, tmp_path)

    assert [pano["panoid"] for pano in panos] == [inside]
    assert len(calls) == 2
    assert calls[0][0] == "https://api.panoramax.xyz/api/search"
    assert calls[0][1]["limit"] == 1000
    assert json.loads((tmp_path / "pano-crawl.json").read_text())["skipped_counts"]["license"] == 2
    assert json.loads((tmp_path / "pano-crawl.json").read_text())["returned_count"] == 1


def test_discover_fails_explicitly_on_saturated_coverage_and_audits(tmp_path, monkeypatch):
    features = [_feature("c59ab61f-0a62-4f27-8724-017df8c2730a", 2.0, 49.0)]
    payload = {"type": "FeatureCollection", "features": features, "links": []}

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse(payload))
    monkeypatch.setenv("NETRYX_PANORAMAX_SEARCH_LIMIT", "1")

    with pytest.raises(RuntimeError, match="PANORAMAX_COVERAGE_TRUNCATED.*narrow radius"):
        panoramax.discover([(49.0, 2.0)], 100, tmp_path)

    audit = json.loads((tmp_path / "pano-crawl.json").read_text())
    assert audit["coverage_status"] == "saturated"
    assert audit["queries"][0]["coverage_saturated"] is True


def test_discover_fails_with_clear_no_coverage_and_keeps_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse({"type": "FeatureCollection", "features": [], "links": []}),
    )

    with pytest.raises(RuntimeError, match="PANORAMAX_NO_COVERAGE"):
        panoramax.discover([(49.0, 2.0)], 100, tmp_path)

    audit = json.loads((tmp_path / "pano-crawl.json").read_text())
    assert audit["coverage_status"] == "empty"
    assert audit["returned_count"] == 0


def test_discover_falls_back_to_hd_and_skips_untrusted_assets(tmp_path, monkeypatch):
    hd_id = "c59ab61f-0a62-4f27-8724-017df8c2730a"
    bad_id = "04b7e9cc-76d0-4d42-85f0-1b2f7c8da1e3"
    hd_feature = _feature(hd_id, 2.0, 49.0)
    hd_feature["assets"] = {
        "hd": {"href": f"https://panoramax.openstreetmap.fr/api/pictures/{hd_id}/hd.jpg"}
    }
    bad_feature = _feature(bad_id, 2.0001, 49.0001, href="https://evil.example/picture.jpg")
    payload = {"features": [hd_feature, bad_feature], "links": []}
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse(payload))

    panos = panoramax.discover([(49.0, 2.0)], 100, tmp_path)

    assert len(panos) == 1
    assert panos[0]["image_url"].endswith("/hd.jpg")
    audit = json.loads((tmp_path / "pano-crawl.json").read_text())
    assert audit["skipped_counts"]["asset"] == 1


def test_discover_fails_when_asset_redirect_leaves_trusted_origins(tmp_path, monkeypatch):
    class RedirectResponse:
        status_code = 302
        headers = {"location": "https://evil.example/image.jpg"}

        def raise_for_status(self):
            return None

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: RedirectResponse())
    with pytest.raises(ValueError, match="untrusted origin"):
        panoramax._download_image(
            "https://panoramax.ign.fr/api/pictures/c59ab61f-0a62-4f27-8724-017df8c2730a/sd.jpg",
            tmp_path / "image.jpg",
            panoramic=False,
        )


def test_build_index_rejects_empty_discovery_without_loading_models(tmp_path):
    with pytest.raises(RuntimeError, match="PANORAMAX_NO_USABLE_IMAGES"):
        panoramax.build_index([], tmp_path, [0])


def test_build_index_uses_projection_and_preserves_local_orientation(tmp_path, monkeypatch):
    import numpy as np
    from PIL import Image

    panoid = "c59ab61f-0a62-4f27-8724-017df8c2730a"
    cache_dir = tmp_path / "panoramax-images"
    cache_dir.mkdir()
    Image.new("RGB", (100, 50), "red").save(cache_dir / f"{panoid}.jpg", format="JPEG")

    class NoGrad:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeTorch:
        @staticmethod
        def no_grad():
            return NoGrad()

    class FakeCrops:
        def __init__(self, images):
            self.images = images

        def __getitem__(self, item):
            if isinstance(item, slice):
                return self.images[item.start]
            return self.images[item]

    def project(image, _fov, _size, yaws, _pitch, _base_dirs):
        return FakeCrops([image.copy() for _ in yaws])

    def components():
        return (
            FakeTorch,
            project,
            lambda _fov, _size: None,
            lambda image: image,
            lambda crop: crop,
            lambda images, batch_size=8: np.asarray([[3.0, 4.0] for _ in images]),
        )

    monkeypatch.setattr(panoramax, "_load_indexing_components", components)
    pano = {
        "panoid": panoid,
        "lat": 49.0,
        "lon": 2.0,
        "image_url": f"https://panoramax.ign.fr/api/pictures/{panoid}/sd.jpg",
        "source_url": "https://panoramax.ign.fr/pictures/" + panoid,
        "license": "etalab-2.0",
        "license_url": "https://example.test/etalab",
        "attribution": ["Test producer"],
        "datetime": "2024-01-02T03:04:05Z",
        "camera_azimuth": 33,
        "field_of_view": 360,
        "perspective": False,
    }

    descriptors, metadata, downloaded = panoramax.build_index([pano], tmp_path, [0, 90])

    assert descriptors.shape == (2, 2)
    assert np.allclose(np.linalg.norm(descriptors, axis=1), 1.0)
    assert downloaded == 1
    assert [row["heading"] for row in metadata] == [0.0, 90.0]
    assert [row["camera_azimuth"] for row in metadata] == [33.0, 33.0]
    assert all(row["heading_reference"] == "image_center" for row in metadata)
    assert all(row["perspective"] is False for row in metadata)
    assert metadata[0]["path"].endswith(f"panoramax-images/{panoid}.jpg")
    assert metadata[0]["attribution"] == ["Test producer"]
    assert (tmp_path / "global-index.npz").is_file()


def test_build_index_downloads_flat_photo_and_uses_geographic_heading(tmp_path, monkeypatch):
    import io
    import numpy as np
    from PIL import Image

    payload = io.BytesIO()
    Image.new("RGB", (80, 60), "blue").save(payload, format="JPEG")
    image_bytes = payload.getvalue()

    class ImageResponse:
        status_code = 200
        headers = {"content-type": "image/jpeg", "content-length": str(len(image_bytes))}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            for start in range(0, len(image_bytes), 7):
                yield image_bytes[start : start + 7]

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return ImageResponse()

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr(
        panoramax,
        "_load_indexing_components",
        lambda: (
            type("Torch", (), {"no_grad": staticmethod(lambda: _NoGrad())}),
            lambda image, *_args: [image.copy()],
            lambda *_args: None,
            lambda image: image,
            lambda crop: crop,
            lambda images, batch_size=8: np.asarray([[1.0, 0.0] for _ in images]),
        ),
    )

    panoid = "04b7e9cc-76d0-4d42-85f0-1b2f7c8da1e3"
    pano = {
        "panoid": panoid,
        "lat": 49.0,
        "lon": 2.0,
        "image_url": "https://panoramax.openstreetmap.fr/api/pictures/" + panoid + "/hd.jpg",
        "source_url": "https://panoramax.openstreetmap.fr/pictures/" + panoid,
        "license": "CC-BY-SA-4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": ["OSM producer"],
        "datetime": "2024-02-03T04:05:06Z",
        "camera_azimuth": 271,
        "field_of_view": 90,
        "perspective": True,
    }

    descriptors, metadata, downloaded = panoramax.build_index([pano], tmp_path, [0, 90])

    assert descriptors.shape == (1, 2)
    assert downloaded == 1
    assert len(metadata) == 1
    assert metadata[0]["heading"] == 271.0
    assert metadata[0]["perspective"] is True
    assert metadata[0]["heading_reference"] == "geographic"
    assert metadata[0]["license"] == "CC-BY-SA-4.0"
    assert calls[0][1]["stream"] is True
    assert calls[0][1]["allow_redirects"] is False


class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_hd_mode_selects_full_resolution_and_separates_cache(tmp_path, monkeypatch):
    from PIL import Image

    panoid = "c59ab61f-0a62-4f27-8724-017df8c2730a"
    feature = _feature(panoid, 2.0, 49.0)
    hd_url = f"https://panoramax.ign.fr/api/pictures/{panoid}/hd.jpg"
    feature["assets"]["hd"] = {"href": hd_url}
    monkeypatch.setenv("NETRYX_PANORAMAX_IMAGE_SIZE", "hd")
    pano, reason = panoramax._normalise_feature(feature)
    assert reason is None
    assert pano is not None
    assert pano["image_url"] == hd_url
    assert pano["image_variant"] == "hd"
    sd_cache = tmp_path / "panoramax-images" / f"{panoid}.jpg"
    sd_cache.parent.mkdir()
    Image.new("RGB", (100, 50)).save(sd_cache)
    calls = []

    def download(url, destination, **kwargs):
        calls.append(url)
        return destination

    monkeypatch.setattr(panoramax, "_download_image", download)
    path = panoramax._ensure_cached_image(pano, tmp_path, panoramic=True)
    assert path.parent.name == "panoramax-images-hd"
    assert calls == [hd_url]


def test_camera_mpo_jpeg_is_a_valid_panorama(tmp_path):
    from PIL import Image

    path = tmp_path / "camera.jpg"
    first = Image.new("RGB", (80, 40), "blue")
    second = Image.new("RGB", (80, 40), "white")
    first.save(path, format="MPO", save_all=True, append_images=[second])
    assert panoramax._valid_image(path, panoramic=True)


def test_all_failed_downloads_keep_diagnostic_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(panoramax, "_load_indexing_components", lambda: (None,) * 6)

    def fail(*args, **kwargs):
        raise ValueError("invalid panorama format")

    monkeypatch.setattr(panoramax, "_ensure_cached_image", fail)
    (tmp_path / "pano-crawl.json").write_text(json.dumps({"provider": "panoramax"}))
    with pytest.raises(RuntimeError, match="PANORAMAX_NO_USABLE_IMAGES"):
        panoramax.build_index([{"panoid": "test", "perspective": False}], tmp_path, [0])
    audit = json.loads((tmp_path / "pano-crawl.json").read_text())
    assert audit["index_status"] == "failed"
    assert audit["index_errors"][0]["message"] == "invalid panorama format"


def test_flat_photo_without_compass_has_usable_but_unknown_heading(tmp_path):
    metadata = panoramax._source_metadata(
        {"panoid": "test", "lat": 48, "lon": 2}, tmp_path / "photo.jpg",
        panoramic=False, heading=None,
    )
    # Verification needs a numeric image-local heading; it must not claim north.
    assert metadata["heading"] == 0
    assert metadata["heading_reference"] == "unknown"


def test_trusted_provenance_url_uses_hostname_boundaries():
    accepted = "https://images.panoramax.ign.fr/pictures/test"
    accepted_default_port = "https://panoramax.ign.fr:443/pictures/test"

    assert panoramax.trusted_provenance_url(accepted) == accepted
    assert panoramax.trusted_provenance_url(accepted_default_port) == accepted_default_port
    for value in (
        "https://evilpanoramax.example/pictures/test",
        "https://panoramax.evil.example/pictures/test",
        "https://panoramax.com.evil/pictures/test",
        "https://user:pass@panoramax.ign.fr/pictures/test",
        "https://panoramax.ign.fr:8443/pictures/test",
    ):
        assert panoramax.trusted_provenance_url(value) is None


def test_normalise_feature_falls_back_to_trusted_asset_for_untrusted_source_url():
    panoid = "c59ab61f-0a62-4f27-8724-017df8c2730a"
    feature = _feature(panoid, 2.0, 49.0)
    feature["properties"]["source_url"] = "https://panoramax.com.evil/pictures/test"

    pano, reason = panoramax._normalise_feature(feature)

    assert reason is None
    assert pano is not None
    assert pano["source_url"] == pano["image_url"]
