import test from "node:test";
import assert from "node:assert/strict";

import { validateApiOrigin, validateListingUrl, validatePhotoSubmission } from "../js/validation.js";

test("accepte une URL HTTPS publique", () => {
  assert.deepEqual(validateListingUrl("https://www.leboncoin.fr/ad/123"), {
    valid: true,
    value: "https://www.leboncoin.fr/ad/123",
  });
});

test("rejette les URL vides, locales, avec identifiants ou protocole dangereux", () => {
  for (const value of [
    "",
    "javascript:alert(1)",
    "http://127.0.0.1/listing",
    "http://localhost/listing",
    "https://user:secret@example.com/listing",
  ]) {
    assert.equal(validateListingUrl(value).valid, false, value);
  }
});

function image(name, type = "image/jpeg", size = 1024) {
  return { name, type, size };
}

test("refuse une recherche photo sans coordonnées complètes", () => {
  const result = validatePhotoSubmission({
    photos: [image("facade.jpg")],
    latitude: "",
    longitude: "2.35",
    radiusM: "300",
    reviewedExterior: true,
    imageSize: "hd",
  });
  assert.equal(result.valid, false);
  assert.match(result.message, /latitude/i);
});

test("valide la recherche photo et conserve ses bornes contractuelles", () => {
  const result = validatePhotoSubmission({
    photos: [image("facade.jpg"), image("angle.png", "image/png")],
    latitude: "48.8566",
    longitude: "2.3522",
    radiusM: "300",
    reviewedExterior: true,
    imageSize: "hd",
  });
  assert.equal(result.valid, true);
  assert.deepEqual({ ...result.value, photos: result.value.photos }, {
    photos: result.value.photos,
    latitude: 48.8566,
    longitude: 2.3522,
    radiusM: 300,
    reviewedExterior: true,
    imageSize: "hd",
  });
});

test("refuse les fichiers non image, trop lourds et une revue extérieure non confirmée", () => {
  for (const input of [
    { photos: [], reviewedExterior: true },
    { photos: [image("plan.pdf", "application/pdf")], reviewedExterior: true },
    { photos: [image("huge.jpg", "image/jpeg", 10 * 1024 * 1024 + 1)], reviewedExterior: true },
    { photos: [image("inside.jpg")], reviewedExterior: false },
  ]) {
    const result = validatePhotoSubmission({
      ...input,
      latitude: "48",
      longitude: "2",
      radiusM: "50",
      imageSize: "sd",
    });
    assert.equal(result.valid, false);
  }
});

test("n’autorise que les origines API publiques HTTPS ou les hôtes locaux", () => {
  assert.equal(validateApiOrigin("https://api.example.test").valid, true);
  assert.equal(validateApiOrigin("https://api.example.test/path").valid, false);
  assert.equal(validateApiOrigin("http://localhost:8000").valid, true);
  assert.equal(validateApiOrigin("http://api.example.test").valid, false);
});
