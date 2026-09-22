import json
import tempfile
import unittest
from pathlib import Path

from netryx_web.research import ArtifactResearchPipeline, ResearchError, ResearchService, build_research_service


class ArtifactResearchPipelineTests(unittest.TestCase):
    def test_build_service_defaults_to_dynamic_live_runner(self):
        import os

        previous = os.environ.pop("NETRYX_RESEARCH_COMMAND", None)
        try:
            service = build_research_service(
                results_root=Path("/tmp/no-artifacts"),
                work_root=Path("/tmp/netryx-work"),
                listing_fetcher=object(),
            )
        finally:
            if previous is not None:
                os.environ["NETRYX_RESEARCH_COMMAND"] = previous
        self.assertIsNotNone(service.live_pipeline)
        self.assertTrue(service.live_pipeline.command[-1].endswith("live_runner.py"))

    def test_builds_result_from_dynamic_ranked_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "987654321" / "headless"
            out.mkdir(parents=True)
            payload = {
                "panoids_indexed": 76,
                "global_entries": 608,
                "combined_ranked": [
                    {
                        "panoid": "public-pano-id",
                        "lat": 49.35729,
                        "lon": 2.79770,
                        "query_support": 2,
                        "sum_inliers": 100,
                        "best_inliers": 56,
                        "sources": [
                            {"image": "13", "heading": 270, "inliers": 56},
                            {"image": "14", "heading": 285, "inliers": 44},
                        ],
                    }
                ],
            }
            (out / "ranked_coordinates.json").write_text(json.dumps(payload))

            pipeline = ArtifactResearchPipeline(
                root,
                reverse_geocode=lambda lat, lon: "20 rue Exemple, 60610 Ville",
            )
            result = pipeline.run(
                "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                lambda *_: None,
            )

            self.assertEqual(result["location"]["label_fr"], "20 rue Exemple, 60610 Ville")
            self.assertEqual(result["confidence"]["level"], "HIGH")
            self.assertLess(result["location"]["longitude"], 2.79770)
            self.assertIn("google.com/maps", result["google_maps_url"])
            self.assertTrue(any(item["kind"] == "geometric_match" for item in result["evidence"]))

    def test_replay_preserves_structured_panoramax_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "987654321" / "headless"
            out.mkdir(parents=True)
            (out / "ranked_coordinates.json").write_text(
                json.dumps(
                    {
                        "combined_ranked": [
                            {
                                "lat": 49.0,
                                "lon": 2.0,
                                "query_support": 1,
                                "sum_inliers": 10,
                                "best_inliers": 10,
                                "sources": [
                                    {
                                        "heading": 0,
                                        "inliers": 10,
                                        "provider": "panoramax",
                                        "source_url": "https://panoramax.ign.fr/pictures/replay",
                                        "license": "etalab-2.0",
                                        "license_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
                                        "attribution": ["Replay producer"],
                                    },
                                    {
                                        "heading": 15,
                                        "inliers": 9,
                                        "provider": "panoramax",
                                        "source_url": "https://panoramax.evil.example/pictures/replay",
                                        "license": "etalab-2.0",
                                    },
                                ],
                            }
                        ]
                    }
                )
            )

            result = ArtifactResearchPipeline(root, reverse_geocode=lambda *_: None).run(
                "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                lambda *_: None,
            )

            assert result is not None
            assert result["sources"] == [
                {
                    "provider": "panoramax",
                    "source_url": "https://panoramax.ign.fr/pictures/replay",
                    "license": "etalab-2.0",
                    "license_url": "https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
                    "attribution": ["Replay producer"],
                }
            ]
            assert result["panoramax_url"] == "https://panoramax.ign.fr/pictures/replay"

    def test_replay_preserves_candidate_level_panoramax_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "987654321" / "headless"
            out.mkdir(parents=True)
            (out / "ranked_coordinates.json").write_text(
                json.dumps(
                    {
                        "combined_ranked": [
                            {
                                "lat": 49.0,
                                "lon": 2.0,
                                "query_support": 1,
                                "sum_inliers": 10,
                                "best_inliers": 10,
                                "provider": "panoramax",
                                "source_url": "https://panoramax.openstreetmap.fr/pictures/replay",
                                "license": "CC-BY-SA-4.0",
                                "sources": [{"heading": 0, "inliers": 10}],
                            }
                        ]
                    }
                )
            )

            result = ArtifactResearchPipeline(root, reverse_geocode=lambda *_: None).run(
                "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                lambda *_: None,
            )

            assert result is not None
            assert result["sources"] == [
                {
                    "provider": "panoramax",
                    "source_url": "https://panoramax.openstreetmap.fr/pictures/replay",
                    "license": "CC-BY-SA-4.0",
                }
            ]

    def test_replay_rejects_mixed_malformed_provenance_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "987654321" / "headless"
            out.mkdir(parents=True)
            (out / "ranked_coordinates.json").write_text(
                json.dumps(
                    {
                        "combined_ranked": [
                            {
                                "lat": 49.0,
                                "lon": 2.0,
                                "query_support": 1,
                                "sum_inliers": 10,
                                "best_inliers": 10,
                                "sources": [{"heading": 0, "inliers": 10}, 123],
                            }
                        ]
                    }
                )
            )

            with self.assertRaises(ResearchError) as context:
                ArtifactResearchPipeline(root, reverse_geocode=lambda *_: None).run(
                    "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                    lambda *_: None,
                )

            self.assertEqual(context.exception.code, "INVALID_RESEARCH_ARTIFACT")

    def test_replay_rejects_falsy_scalar_provenance_sources(self):
        for malformed_sources in (0, False, "", {}):
            with self.subTest(sources=malformed_sources), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                out = root / "987654321" / "headless"
                out.mkdir(parents=True)
                (out / "ranked_coordinates.json").write_text(
                    json.dumps({
                        "combined_ranked": [{
                            "lat": 49.0,
                            "lon": 2.0,
                            "sources": malformed_sources,
                        }]
                    })
                )

                with self.assertRaises(ResearchError) as context:
                    ArtifactResearchPipeline(root, reverse_geocode=lambda *_: None).run(
                        "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                        lambda *_: None,
                    )

                self.assertEqual(context.exception.code, "INVALID_RESEARCH_ARTIFACT")

    def test_replay_rejects_non_numeric_source_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "987654321" / "headless"
            out.mkdir(parents=True)
            (out / "ranked_coordinates.json").write_text(
                json.dumps({
                    "combined_ranked": [{
                        "lat": 49.0,
                        "lon": 2.0,
                        "sources": [{"heading": "north"}],
                    }]
                })
            )

            with self.assertRaises(ResearchError) as context:
                ArtifactResearchPipeline(root, reverse_geocode=lambda *_: None).run(
                    "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                    lambda *_: None,
                )

            self.assertEqual(context.exception.code, "INVALID_RESEARCH_ARTIFACT")

    def test_replay_rejects_non_finite_numeric_fields(self):
        malformed_candidates = (
            {"lat": "nan", "lon": 2.0, "sources": []},
            {"lat": 49.0, "lon": 2.0, "sources": [{"heading": "inf"}]},
        )
        for candidate in malformed_candidates:
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                out = root / "987654321" / "headless"
                out.mkdir(parents=True)
                (out / "ranked_coordinates.json").write_text(
                    json.dumps({"combined_ranked": [candidate]})
                )

                with self.assertRaises(ResearchError) as context:
                    ArtifactResearchPipeline(root, reverse_geocode=lambda *_: None).run(
                        "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                        lambda *_: None,
                    )

                self.assertEqual(context.exception.code, "INVALID_RESEARCH_ARTIFACT")

    def test_returns_none_when_no_artifact_matches_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipeline = ArtifactResearchPipeline(Path(tmp))
            self.assertIsNone(
                pipeline.run(
                    "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                    lambda *_: None,
                )
            )

    def test_replay_still_checks_syndication_and_marks_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "987654321" / "headless"
            out.mkdir(parents=True)
            (out / "ranked_coordinates.json").write_text(
                json.dumps(
                    {
                        "combined_ranked": [
                            {
                                "lat": 49.0,
                                "lon": 2.0,
                                "query_support": 1,
                                "sum_inliers": 10,
                                "best_inliers": 10,
                                "sources": [{"heading": 0, "inliers": 10}],
                            }
                        ]
                    }
                )
            )

            class Discoverer:
                def __init__(self):
                    self.calls = []

                def discover(self, request):
                    self.calls.append(request)
                    return {"status": "not_found", "provider": "test", "candidates": []}

            discoverer = Discoverer()
            service = ResearchService(
                ArtifactResearchPipeline(root, reverse_geocode=lambda *_: None),
                syndication_discoverer=discoverer,
            )
            result = service.run(
                "https://www.leboncoin.fr/ad/ventes_immobilieres/987654321",
                lambda *_: None,
            )

            self.assertEqual(result["provenance"]["research_mode"], "replay")
            self.assertEqual(result["provenance"]["syndication"]["status"], "not_found")
            self.assertEqual(discoverer.calls[0].listing_id, "987654321")


if __name__ == "__main__":
    unittest.main()
