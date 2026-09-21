"""Local photo entry point: python -m netryx_web.locate_photo --help."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import sys
from pathlib import Path

from .live_runner import _run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare exterior photos to local street imagery.")
    parser.add_argument("photos", type=Path, nargs="+")
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--radius", type=float, default=300, help="Search radius in metres, not accuracy")
    parser.add_argument("--work-dir", type=Path, required=True, help="Dedicated directory for this search")
    parser.add_argument("--reviewed-exterior", action="store_true",
                        help="Confirm you visually checked ALL supplied photos are exteriors")
    args = parser.parse_args(argv)
    if not (-90 < args.latitude < 90 and -180 <= args.longitude <= 180):
        parser.error("invalid search coordinates")
    if not math.isfinite(args.radius) or args.radius <= 0:
        parser.error("radius must be finite and positive")
    images = []
    for photo in args.photos:
        path = photo.resolve()
        if not path.is_file():
            parser.error(f"photo does not exist: {path}")
        images.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {
        "acquisition": "local_photos",
        "images": images,
        "center": {"latitude": args.latitude, "longitude": args.longitude},
        "search_radius_m": args.radius,
    }
    if args.reviewed_exterior:
        manifest["exterior_review"] = {
            "approved_sha256": [item["sha256"] for item in images],
            "reviewer": "local operator (--reviewed-exterior)",
        }
    args.work_dir.mkdir(parents=True, exist_ok=True)
    (args.work_dir / "result.json").unlink(missing_ok=True)
    manifest_path = args.work_dir.resolve() / "listing-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = _run(manifest_path)
    except Exception as exc:
        print(f"photo search failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    (args.work_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
