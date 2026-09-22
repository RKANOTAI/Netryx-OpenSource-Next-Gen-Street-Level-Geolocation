import test from "node:test";
import assert from "node:assert/strict";

import { normalizeJobSnapshot } from "../js/contract.js";
import { confidenceLabel, coordinatesLabel, googleMapsUrl, safeMapsUrl } from "../js/formatters.js";

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
      panoramax_url: "https://api.panoramax.xyz/api/items/123",
      sources: [
        { label_fr: "Vue Panoramax", url: "https://panoramax.openstreetmap.fr/123" },
        { label_fr: "Lien refusé", url: "javascript:alert(1)" },
      ],
    },
  });
  assert.equal(result.result.panoramax_url, "https://api.panoramax.xyz/api/items/123");
  assert.deepEqual(result.result.sources, [{ label_fr: "Vue Panoramax", url: "https://panoramax.openstreetmap.fr/123" }]);
});

test("rejette les domaines ressemblants, identifiants et ports Panoramax non approuvés", () => {
  const snapshot = normalizeJobSnapshot({
    job_id: "x",
    status: "succeeded",
    result: {
      summary_fr: "Estimation caméra.",
      location: { latitude: 48, longitude: 2 },
      confidence: { level: "MEDIUM" },
      evidence: [{ label_fr: "Texte", detail_fr: "https://panoramax.evil.example/photo/1" }],
      panoramax_url: "https://evilpanoramax.example/photo/1",
      sources: [
        { url: "https://panoramax.evil.example/photo/2" },
        { url: "https://panoramax.com.evil/photo/3" },
        { url: "https://user:secret@panoramax.xyz/photo/4" },
        { url: "https://panoramax.xyz:8443/photo/5" },
      ],
    },
  });

  assert.equal(snapshot.result.panoramax_url, null);
  assert.deepEqual(snapshot.result.sources, []);
});

test("déduplique les sources structurées et conserve leur provenance Panoramax", () => {
  const sourceUrl = "https://api.panoramax.xyz/api/items/pano-1";
  const snapshot = normalizeJobSnapshot({
    job_id: "x",
    status: "succeeded",
    result: {
      summary_fr: "Estimation caméra.",
      location: { latitude: 48, longitude: 2 },
      confidence: { level: "MEDIUM" },
      evidence: [],
      sources: [
        {
          provider: "panoramax",
          source_url: sourceUrl,
          license: "CC-BY-SA-4.0",
          attribution: ["Contributeur test"],
        },
        { provider: "panoramax", url: sourceUrl, license: "autre" },
      ],
    },
  });

  assert.equal(snapshot.result.panoramax_url, sourceUrl);
  assert.deepEqual(snapshot.result.sources, [{
    provider: "panoramax",
    source_url: sourceUrl,
    license: "CC-BY-SA-4.0",
    attribution: ["Contributeur test"],
    label_fr: "Source Panoramax",
    url: sourceUrl,
  }]);
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

test("refuse les sous-domaines Panoramax inconnus et même le port HTTPS explicite", () => {
  const snapshot = normalizeJobSnapshot({
    job_id: "x",
    status: "succeeded",
    result: {
      summary_fr: "Estimation caméra.",
      location: { latitude: 48, longitude: 2 },
      confidence: { level: "MEDIUM" },
      evidence: [],
      panoramax_url: "https://rogue.panoramax.xyz/photo/1",
      sources: [{ url: "https://api.panoramax.xyz:443/api/items/2" }],
    },
  });

  assert.equal(snapshot.result.panoramax_url, null);
  assert.deepEqual(snapshot.result.sources, []);
});

test("reconstruit les liens Google Maps avec identifiants ou ports explicites", () => {
  const fallback = googleMapsUrl(48.8566, 2.3522);
  assert.equal(
    safeMapsUrl("https://user:secret@www.google.com/maps/search/?api=1&query=48", 48.8566, 2.3522),
    fallback,
  );
  assert.equal(
    safeMapsUrl("https://www.google.com:443/maps/search/?api=1&query=48", 48.8566, 2.3522),
    fallback,
  );
  assert.equal(
    safeMapsUrl("https://maps.google.com:8443/maps/search/?api=1&query=48", 48.8566, 2.3522),
    fallback,
  );
});
