import hashlib

from PIL import Image

from netryx_web.photo_selection import ExteriorDecision, select_exterior_images


def test_hash_bound_exterior_review_accepts_only_reviewed_photo(tmp_path, monkeypatch):
    exterior = tmp_path / "facade.jpg"
    unknown = tmp_path / "unknown.jpg"
    Image.new("RGB", (80, 80), "blue").save(exterior)
    Image.new("RGB", (80, 80), "white").save(unknown)
    digest = hashlib.sha256(exterior.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "netryx_web.photo_selection.ImageNetExteriorClassifier",
        lambda: lambda _: ExteriorDecision(0.1, 0.1, "REJECT_UNCERTAIN", "uncertain"),
    )
    selected, audit = select_exterior_images({
        "images": [{"path": str(exterior)}, {"path": str(unknown)}],
        "exterior_review": {
            "approved_sha256": [digest],
            "reviewer": "local operator",
        },
    })
    assert selected == [exterior]
    assert audit[0]["decision"] == "ACCEPT_EXTERIOR"
    assert "review" in audit[0]["reason"]
    assert audit[1]["decision"] == "REJECT_UNCERTAIN"


def test_review_cannot_be_reused_for_changed_bytes_or_claimed_hash(tmp_path):
    photo = tmp_path / "changed.jpg"
    Image.new("RGB", (80, 80), "blue").save(photo)
    original_hash = hashlib.sha256(photo.read_bytes()).hexdigest()
    Image.new("RGB", (80, 80), "white").save(photo)
    selected, _ = select_exterior_images(
        {
            "images": [{"path": str(photo), "sha256": original_hash}],
            "exterior_review": {"approved_sha256": [original_hash], "reviewer": "operator"},
        },
        classifier=lambda _: ExteriorDecision(0, 0, "REJECT_UNCERTAIN", "uncertain"),
    )
    assert selected == []


def test_reviewed_image_needs_no_classifier_checkpoint(tmp_path, monkeypatch):
    photo = tmp_path / "facade.jpg"
    Image.new("RGB", (80, 80), "blue").save(photo)

    def unavailable():
        raise AssertionError("reviewed photos should not load a classifier")

    monkeypatch.setattr("netryx_web.photo_selection.ImageNetExteriorClassifier", unavailable)
    selected, _ = select_exterior_images({
        "images": [{"path": str(photo)}],
        "exterior_review": {
            "approved_sha256": [hashlib.sha256(photo.read_bytes()).hexdigest()],
            "reviewer": "operator",
        },
    })
    assert selected == [photo]
