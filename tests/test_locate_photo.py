import json

from PIL import Image
import pytest


def test_photo_command_passes_exact_center_and_reviewed_hash(tmp_path, monkeypatch, capsys):
    from netryx_web import locate_photo

    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (80, 80), "blue").save(photo)
    captured = {}

    def run(path):
        captured.update(json.loads(path.read_text()))
        return {"test_result": True}

    monkeypatch.setattr(locate_photo, "_run", run)
    assert locate_photo.main([
        str(photo), "--latitude", "48.5", "--longitude", "2.5",
        "--radius", "150", "--work-dir", str(tmp_path / "work"),
        "--reviewed-exterior",
    ]) == 0
    assert captured["center"] == {"latitude": 48.5, "longitude": 2.5}
    assert captured["search_radius_m"] == 150
    assert captured["images"][0]["path"] == str(photo)
    assert len(captured["exterior_review"]["approved_sha256"]) == 1
    assert json.loads(capsys.readouterr().out) == {"test_result": True}


@pytest.mark.parametrize("latitude,radius", [("91", "150"), ("nan", "150"), ("48", "0")])
def test_photo_command_rejects_invalid_region(tmp_path, latitude, radius):
    from netryx_web import locate_photo

    with pytest.raises(SystemExit) as error:
        locate_photo.main(["photo.jpg", "--latitude", latitude, "--longitude", "2.5",
                           "--radius", radius, "--work-dir", str(tmp_path)])
    assert error.value.code == 2


def test_failed_rerun_cannot_leave_a_stale_success(tmp_path, monkeypatch):
    from netryx_web import locate_photo

    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (80, 80)).save(photo)
    work = tmp_path / "work"
    work.mkdir()
    (work / "result.json").write_text('{"old_result": true}')

    def fail(path):
        raise RuntimeError("no coverage")

    monkeypatch.setattr(locate_photo, "_run", fail)
    assert locate_photo.main([str(photo), "--latitude", "48", "--longitude", "2",
                              "--work-dir", str(work)]) == 1
    assert not (work / "result.json").exists()
