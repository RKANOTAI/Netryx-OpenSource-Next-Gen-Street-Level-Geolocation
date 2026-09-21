from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


class ExteriorClassifierUnavailable(RuntimeError):
    """Raised when the pinned exterior classifier is not available locally."""


@dataclass(frozen=True)
class ExteriorDecision:
    exterior_score: float
    interior_score: float
    decision: str
    reason: str


class ExteriorClassifier(Protocol):
    def __call__(self, path: Path) -> ExteriorDecision: ...


# These are exact ImageNet category labels, not substring matches.
EXTERIOR_LABELS = frozenset(
    {
        "street sign",
        "traffic light",
        "streetcar",
        "steel arch bridge",
        "suspension bridge",
        "castle",
        "church",
        "mosque",
        "palace",
        "water tower",
        "barn",
        "boathouse",
        "dam",
        "dock",
        "pier",
        "lakeside",
        "seashore",
        "valley",
        "volcano",
        "greenhouse",
    }
)

INTERIOR_LABELS = frozenset(
    {
        "home theater",
        "cinema",
        "barbershop",
        "bathtub",
        "shower curtain",
        "toilet seat",
        "wardrobe",
        "pool table",
        "theater curtain",
        "dining table",
        "studio couch",
    }
)


class ImageNetExteriorClassifier:
    """Conservative, local-only exterior gate using cached ImageNet weights.

    ImageNet is only a provisional gate. It is deliberately fail-closed and does
    not download weights at runtime. A calibrated scene classifier should replace
    it for production deployments.
    """

    def __init__(self) -> None:
        try:
            import torch
            from torchvision.models import ResNet50_Weights, resnet50
        except ImportError as exc:  # pragma: no cover - deployment dependent
            raise ExteriorClassifierUnavailable(
                "torch and torchvision are required for exterior photo selection"
            ) from exc

        weights = ResNet50_Weights.DEFAULT
        checkpoint = Path(torch.hub.get_dir()) / "checkpoints" / Path(weights.url).name
        if not checkpoint.is_file():
            raise ExteriorClassifierUnavailable(
                f"cached exterior classifier weights are missing: {checkpoint}"
            )

        self._torch = torch
        self._weights = weights
        self._categories = tuple(weights.meta["categories"])
        self._model = resnet50(weights=weights).eval()
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._model.to(self._device)
        self._transform = weights.transforms()

    def __call__(self, path: Path) -> ExteriorDecision:
        from PIL import Image

        with Image.open(path) as image:
            tensor = self._transform(image.convert("RGB")).unsqueeze(0).to(self._device)
        with self._torch.inference_mode():
            probabilities = self._torch.softmax(self._model(tensor), dim=1)[0]

        exterior_indices = [
            index for index, label in enumerate(self._categories) if label in EXTERIOR_LABELS
        ]
        interior_indices = [
            index for index, label in enumerate(self._categories) if label in INTERIOR_LABELS
        ]
        exterior_score = float(probabilities[exterior_indices].max()) if exterior_indices else 0.0
        interior_score = float(probabilities[interior_indices].max()) if interior_indices else 0.0
        margin = exterior_score - interior_score
        if interior_score > 0.25:
            decision, reason = "REJECT_INTERIOR", "strong interior category veto"
        elif exterior_score >= 0.60 and margin >= 0.25 and interior_score <= 0.25:
            decision, reason = "ACCEPT_EXTERIOR", "high-confidence exterior category"
        else:
            decision, reason = "REJECT_UNCERTAIN", "exterior classification is not decisive"
        return ExteriorDecision(exterior_score, interior_score, decision, reason)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_item(item: dict[str, Any], *, decision: str, reason: str, **scores: float) -> dict[str, Any]:
    result = {
        "manifest_index": item.get("manifest_index"),
        "path": item.get("path"),
        "sha256": item.get("sha256"),
        "decision": decision,
        "reason": reason,
    }
    result.update(scores)
    return result


def select_exterior_images(
    manifest: dict[str, Any],
    *,
    limit: int = 4,
    classifier: ExteriorClassifier | Callable[[Path], ExteriorDecision] | None = None,
    audit_path: str | Path | None = None,
) -> tuple[list[Path], list[dict[str, Any]]]:
    """Select only high-confidence exterior images; uncertain images are rejected."""

    if limit <= 0:
        return [], []
    active_classifier = classifier
    review = manifest.get("exterior_review") or {}
    approved_hashes = set(review.get("approved_sha256") or []) if review.get("reviewer") else set()
    selected: list[Path] = []
    audit: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    seen_hashes: set[str] = set()

    for index, raw_item in enumerate(manifest.get("images") or []):
        item = dict(raw_item) if isinstance(raw_item, dict) else {}
        item["manifest_index"] = index
        path_value = item.get("path")
        if not isinstance(path_value, str):
            audit.append(_audit_item(item, decision="REJECT_INVALID", reason="missing image path"))
            continue
        path = Path(path_value).resolve()
        item["path"] = str(path)
        if path in seen_paths or not path.is_file():
            audit.append(_audit_item(item, decision="REJECT_INVALID", reason="missing or duplicate path"))
            continue
        seen_paths.add(path)

        try:
            from PIL import Image

            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if image.width < 64 or image.height < 64:
                    raise ValueError("image dimensions are too small")
            # A review applies to these exact bytes, not a caller-supplied hash.
            digest = _sha256(path)
            item["sha256"] = digest
            if digest in seen_hashes:
                audit.append(_audit_item(item, decision="REJECT_INVALID", reason="duplicate content hash"))
                continue
            seen_hashes.add(digest)
        except Exception as exc:
            audit.append(_audit_item(item, decision="REJECT_INVALID", reason=f"unreadable image: {exc}"))
            continue

        try:
            if digest in approved_hashes:
                decision = ExteriorDecision(
                    1.0, 0.0, "ACCEPT_EXTERIOR",
                    f"explicit exterior review by {review['reviewer']}; not a model score",
                )
            else:
                if active_classifier is None:
                    active_classifier = ImageNetExteriorClassifier()
                decision = active_classifier(path)
            if not isinstance(decision, ExteriorDecision):
                raise TypeError("classifier returned an invalid decision")
        except Exception as exc:
            audit.append(_audit_item(item, decision="REJECT_UNCERTAIN", reason=f"classifier failed: {exc}"))
            continue

        audit.append(
            _audit_item(
                item,
                decision=decision.decision,
                reason=decision.reason,
                exterior_score=decision.exterior_score,
                interior_score=decision.interior_score,
            )
        )
        if decision.decision == "ACCEPT_EXTERIOR" and len(selected) < limit:
            selected.append(path)

    if audit_path is not None:
        Path(audit_path).write_text(
            json.dumps({"selected": len(selected), "limit": limit, "items": audit}, indent=2),
            encoding="utf-8",
        )
    return selected, audit
