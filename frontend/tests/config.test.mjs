import test from "node:test";
import assert from "node:assert/strict";

import {
  API_TOKEN_STORAGE_KEY,
  clearApiBaseOverride,
  clearSessionApiToken,
  getApiBase,
  getSessionApiToken,
  setApiBaseOverride,
  setSessionApiToken,
} from "../js/config.js";

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

test("mémorise et efface le jeton dans le stockage du navigateur", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };

  clearSessionApiToken(storage);
  setApiBaseOverride("https://api.example", storage);
  setSessionApiToken("private-test-token", storage);
  assert.deepEqual(JSON.parse(values.get(API_TOKEN_STORAGE_KEY)), {
    origin: "https://api.example",
    token: "private-test-token",
  });
  assert.equal(getSessionApiToken(storage), "private-test-token");

  clearSessionApiToken(storage);
  assert.equal(values.has(API_TOKEN_STORAGE_KEY), false);
  assert.equal(getSessionApiToken(storage), "");
});

test("conserve le jeton en mémoire lorsque le stockage du navigateur échoue", () => {
  const unavailableStorage = {
    getItem: () => { throw new DOMException("blocked", "SecurityError"); },
    setItem: () => { throw new DOMException("full", "QuotaExceededError"); },
    removeItem: () => { throw new DOMException("blocked", "SecurityError"); },
  };

  assert.doesNotThrow(() => clearSessionApiToken(unavailableStorage));
  assert.doesNotThrow(() => setSessionApiToken("session-fallback", unavailableStorage));
  assert.equal(getSessionApiToken(unavailableStorage), "session-fallback");
  assert.doesNotThrow(() => clearSessionApiToken(unavailableStorage));
});

test("ne réutilise jamais un jeton pour une autre origine API", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };

  clearSessionApiToken(storage);
  setApiBaseOverride("https://api-a.example", storage);
  setSessionApiToken("token-for-a", storage);
  assert.equal(getSessionApiToken(storage), "token-for-a");

  setApiBaseOverride("https://api-b.example", storage);
  assert.equal(getSessionApiToken(storage), "");
  clearSessionApiToken(storage);
});
