import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseHTML } from "linkedom";

import { createView } from "../js/view.js";

async function page() {
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  return parseHTML(html).document;
}

test("affiche la phase de préparation des photos vérifiées", async () => {
  const document = await page();
  createView(document);
  const item = document.querySelector('[data-phase="preparing_photo_manifest"]');
  assert.ok(item);
  assert.equal(item.textContent, "Préparation des photos vérifiées");
});

test("affiche la phase de vérification des copies publiques", async () => {
  const document = await page();
  createView(document);
  const item = document.querySelector('[data-phase="checking_syndication"]');
  assert.ok(item);
  assert.equal(item.textContent, "Vérification des copies publiques");
});

test("synchronise la région et la revue extérieure depuis l’état", async () => {
  const document = await page();
  const view = createView(document);
  view.render({
    phase: "idle",
    mode: "photos",
    photos: [],
    region: { latitude: "48,8566", longitude: "2,3522", radiusM: "300" },
    reviewedExterior: true,
    progress: null,
    result: null,
    error: null,
  });

  assert.equal(document.querySelector("#photo-latitude").value, "48,8566");
  assert.equal(document.querySelector("#photo-longitude").value, "2,3522");
  assert.equal(document.querySelector("#photo-radius").value, "300");
  assert.equal(document.querySelector("#reviewed-exterior").checked, true);
});
