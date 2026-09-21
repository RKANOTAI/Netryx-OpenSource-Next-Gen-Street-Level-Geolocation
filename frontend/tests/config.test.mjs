import test from "node:test";
import assert from "node:assert/strict";

import { clearApiBaseOverride, getApiBase, setApiBaseOverride } from "../js/config.js";

test("utilise l’origine API configurée puis l’override local sans toucher au jeton", () => {
  const storage = new Map();
  const fakeStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  };
  assert.equal(getApiBase({ config: { apiBase: "https://configured.example" }, storage: fakeStorage }), "https://configured.example");
  assert.equal(setApiBaseOverride("https://override.example", fakeStorage), "https://override.example");
  assert.equal(getApiBase({ config: { apiBase: "https://configured.example" }, storage: fakeStorage }), "https://override.example");
  clearApiBaseOverride(fakeStorage);
  assert.equal(getApiBase({ config: { apiBase: "https://configured.example" }, storage: fakeStorage }), "https://configured.example");
});
