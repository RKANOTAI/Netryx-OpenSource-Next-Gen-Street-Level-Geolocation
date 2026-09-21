import tempfile
import unittest
from pathlib import Path

from netryx_web.store import JobStore


class JobStoreTests(unittest.TestCase):
    def test_persists_job_lifecycle_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            job = store.create("https://example.com/listing/1")
            self.assertEqual(job["status"], "queued")

            store.update_progress(
                job["job_id"],
                status="running",
                phase="matching",
                percent=60,
                message_fr="Comparaison des vues…",
            )
            running = store.get(job["job_id"])
            self.assertEqual(running["progress"]["percent"], 60)

            result = {
                "summary_fr": "Correspondance trouvée.",
                "location": {"label_fr": "Paris", "latitude": 48.8566, "longitude": 2.3522},
                "confidence": {"level": "HIGH", "score": None},
                "evidence": [],
                "google_maps_url": "https://www.google.com/maps/search/?api=1&query=48.8566%2C2.3522",
            }
            store.succeed(job["job_id"], result)
            stored = store.get(job["job_id"])
            self.assertEqual(stored["status"], "succeeded")
            self.assertEqual(stored["result"], result)
            self.assertEqual(stored["progress"]["percent"], 100)

    def test_claim_next_returns_each_queued_job_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs.sqlite3")
            first = store.create("https://example.com/listing/1")
            store.create("https://example.com/listing/2")
            claimed = store.claim_next()
            self.assertEqual(claimed["job_id"], first["job_id"])
            self.assertEqual(store.get(first["job_id"])["status"], "running")
            self.assertNotEqual(store.claim_next()["job_id"], first["job_id"])


if __name__ == "__main__":
    unittest.main()
