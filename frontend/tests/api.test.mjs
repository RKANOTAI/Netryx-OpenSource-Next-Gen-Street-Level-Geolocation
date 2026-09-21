import test from "node:test";
import assert from "node:assert/strict";

import { checkHealth, createGeolocationJob, createPhotoGeolocationJob, watchGeolocationJob } from "../js/api.js";

test("crée une tâche avec le contrat attendu", async () => {
  let request;
  const fetchImpl = async (url, options) => {
    request = { url, options };
    return new Response(JSON.stringify({ job_id: "geo_1", status: "queued", poll_after_ms: 1 }), {
      status: 202,
      headers: { "content-type": "application/json" },
    });
  };
  const accepted = await createGeolocationJob("https://example.com/listing", { fetchImpl, apiBase: "https://api.example" });
  assert.equal(request.url, "https://api.example/api/v1/geolocations");
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), { listing_url: "https://example.com/listing" });
  assert.equal(accepted.job_id, "geo_1");
});

test("crée une tâche photo multipart avec les quatre champs et le jeton de session", async () => {
  let request;
  const fetchImpl = async (url, options) => {
    request = { url, options };
    return new Response(JSON.stringify({ job_id: "photo_1", status: "queued", poll_after_ms: 2 }), {
      status: 202,
      headers: { "content-type": "application/json" },
    });
  };
  const first = new Blob(["jpeg"], { type: "image/jpeg" });
  const second = new Blob(["png"], { type: "image/png" });
  const accepted = await createPhotoGeolocationJob({
    photos: [first, second],
    latitude: 48.8566,
    longitude: 2.3522,
    radiusM: 300,
    reviewedExterior: true,
    imageSize: "hd",
  }, { fetchImpl, apiBase: "https://api.example", token: "session-secret" });

  assert.equal(request.url, "https://api.example/api/v1/photo-geolocations");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers.Authorization, "Bearer session-secret");
  assert.equal(request.options.headers["Content-Type"], undefined);
  assert.equal(request.options.body.getAll("photos").length, 2);
  assert.equal(request.options.body.get("latitude"), "48.8566");
  assert.equal(request.options.body.get("longitude"), "2.3522");
  assert.equal(request.options.body.get("radius_m"), "300");
  assert.equal(request.options.body.get("reviewed_exterior"), "true");
  assert.equal(request.options.body.get("image_size"), "hd");
  assert.equal(accepted.job_id, "photo_1");
});

test("sonde la santé sans authentification et expose auth_required", async () => {
  const health = await checkHealth("https://api.example", {
    fetchImpl: async () => new Response(JSON.stringify({ status: "ok", auth_required: true }), {
      headers: { "content-type": "application/json" },
    }),
    token: "session-secret",
  });
  assert.deepEqual(health, { status: "ok", auth_required: true });
});

test("arrête le polling après un état terminal", async () => {
  const statuses = ["queued", "running", "succeeded"];
  let calls = 0;
  const fetchImpl = async () => {
    const status = statuses[calls++];
    return new Response(JSON.stringify({
      job_id: "geo_1",
      status,
      progress: status === "succeeded" ? { phase: "complete", percent: 100, message_fr: "Terminé" } : null,
      result: status === "succeeded" ? {
        summary_fr: "Trouvé",
        location: { label_fr: "Paris", latitude: 48.8566, longitude: 2.3522 },
        confidence: { level: "HIGH", score: null },
        evidence: [],
        google_maps_url: "https://www.google.com/maps/search/?api=1&query=48.8566%2C2.3522",
      } : null,
      error: null,
    }), { headers: { "content-type": "application/json" } });
  };
  const updates = [];
  const result = await watchGeolocationJob("geo_1", {
    fetchImpl,
    delay: async () => {},
    pollAfterMs: 1,
    onUpdate: (snapshot) => updates.push(snapshot.status),
  });
  assert.deepEqual(updates, statuses);
  assert.equal(result.status, "succeeded");
  assert.equal(calls, 3);
});
