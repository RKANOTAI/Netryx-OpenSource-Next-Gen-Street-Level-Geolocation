import test from "node:test";
import assert from "node:assert/strict";

import { initialState, reduce } from "../js/state.js";

test("de nouvelles photos nécessitent une nouvelle confirmation extérieure", () => {
  const reviewed = { ...initialState, reviewedExterior: true };
  const next = reduce(reviewed, { type: "PHOTOS_SET", photos: [{ id: "new" }] });
  assert.equal(next.reviewedExterior, false);
});

test("le mode photo est le mode initial et une nouvelle recherche efface l'ancien résultat", () => {
  assert.equal(initialState.mode, "photos");
  const old = { ...initialState, phase: "succeeded", result: { location: "old" } };
  const next = reduce(old, { type: "SUBMIT_START", listingUrl: "https://example.com/1", runId: 2 });
  assert.equal(next.phase, "submitting");
  assert.equal(next.result, null);
  assert.equal(next.listingUrl, "https://example.com/1");
});

test("conserve et retire une photo sélectionnée par son identifiant", () => {
  const current = reduce(initialState, { type: "PHOTOS_SET", photos: [{ id: "one" }, { id: "two" }] });
  const next = reduce(current, { type: "PHOTO_REMOVE", id: "one" });
  assert.deepEqual(next.photos.map((photo) => photo.id), ["two"]);
});

test("ignore la réponse tardive d'un ancien job ou d'une ancienne exécution", () => {
  const current = { ...initialState, phase: "running", jobId: "new", runId: 4 };
  const oldJob = reduce(current, {
    type: "JOB_UPDATED",
    runId: 4,
    snapshot: { job_id: "old", status: "succeeded", result: { value: 1 } },
  });
  const oldRun = reduce(current, {
    type: "JOB_UPDATED",
    runId: 3,
    snapshot: { job_id: "new", status: "succeeded", result: { value: 1 } },
  });
  assert.deepEqual(oldJob, current);
  assert.deepEqual(oldRun, current);
});

test("un résultat not_found ne fabrique aucune localisation", () => {
  const current = { ...initialState, phase: "running", jobId: "one", runId: 1 };
  const next = reduce(current, {
    type: "JOB_UPDATED",
    runId: 1,
    snapshot: { job_id: "one", status: "not_found", result: null, error: null, progress: null },
  });
  assert.equal(next.phase, "not_found");
  assert.equal(next.result, null);
});
