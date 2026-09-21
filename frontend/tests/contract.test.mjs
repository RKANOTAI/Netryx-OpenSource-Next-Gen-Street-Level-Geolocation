import test from "node:test";
import assert from "node:assert/strict";

import { normalizeJobSnapshot } from "../js/contract.js";
import { confidenceLabel, coordinatesLabel, googleMapsUrl } from "../js/formatters.js";

test("valide et normalise une réponse réussie", () => {
  const snapshot = normalizeJobSnapshot({
    job_id: "geo_1",
    status: "succeeded",
    progress: { phase: "complete", percent: 100, message_fr: "Terminé" },
    result: {
      summary_fr: "Correspondance trouvée.",
      location: { label_fr: null, latitude: 48.8566, longitude: 2.3522 },
      confidence: { level: "HIGH", score: null },
      evidence: [],
      google_maps_url: "https://www.google.com/maps/search/?api=1&query=48.8566%2C2.3522",
    },
    error: null,
  });
  assert.equal(snapshot.status, "succeeded");
  assert.equal(snapshot.result.location.latitude, 48.8566);
});

test("rejette les statuts et coordonnées invalides", () => {
  assert.throws(() => normalizeJobSnapshot({ job_id: "x", status: "mystery" }));
  assert.throws(() => normalizeJobSnapshot({
    job_id: "x",
    status: "succeeded",
    result: {
      location: { latitude: 99, longitude: 2 },
      confidence: { level: "HIGH", score: null },
      evidence: [],
      google_maps_url: "",
    },
  }));
});

test("conserve uniquement les liens Panoramax HTTPS fournis par le moteur", () => {
  const result = normalizeJobSnapshot({
    job_id: "x",
    status: "succeeded",
    result: {
      summary_fr: "Estimation caméra.",
      location: { latitude: 48, longitude: 2 },
      confidence: { level: "MEDIUM" },
      evidence: [],
      panoramax_url: "https://panoramax.xyz/photo/123",
      sources: [
        { label_fr: "Vue Panoramax", url: "https://panoramax.openstreetmap.fr/123" },
        { label_fr: "Lien refusé", url: "javascript:alert(1)" },
      ],
    },
  });
  assert.equal(result.result.panoramax_url, "https://panoramax.xyz/photo/123");
  assert.deepEqual(result.result.sources, [{ label_fr: "Vue Panoramax", url: "https://panoramax.openstreetmap.fr/123" }]);
});

test("formate les résultats sans inventer de données", () => {
  assert.equal(confidenceLabel("HIGH"), "Élevée");
  assert.equal(coordinatesLabel(48.8566, 2.3522), "48.856600, 2.352200");
  assert.equal(
    googleMapsUrl(48.8566, 2.3522),
    "https://www.google.com/maps/search/?api=1&query=48.8566%2C2.3522",
  );
  assert.equal(googleMapsUrl(99, 2), null);
});
