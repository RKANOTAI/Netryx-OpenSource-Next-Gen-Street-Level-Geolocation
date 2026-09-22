import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseHTML } from "linkedom";
import { API_TOKEN_STORAGE_KEY } from "../js/config.js";

// DOM wiring test: pure state tests do not catch a renamed form reference.
test("la vraie page démarre, sonde le moteur et valide les deux formulaires", async () => {
  const { document, window } = parseHTML(await readFile(new URL("../index.html", import.meta.url), "utf8"));
  const originalFetch = globalThis.fetch;
  globalThis.document = document;
  globalThis.window = window;
  let connectionScrolled = false;
  document.querySelector("#connection-settings").scrollIntoView = () => { connectionScrolled = true; };
  globalThis.fetch = async () => new Response(JSON.stringify({ status: "ok", auth_required: false }), {
    headers: { "Content-Type": "application/json" },
  });
  try {
    await import("../js/main.js");
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(document.querySelector("#api-status-text").textContent, "Moteur en ligne");
    document.querySelector("#photo-form").dispatchEvent(new window.Event("submit", { cancelable: true }));
    assert.equal(document.querySelector("#photo-error").hidden, false);
    document.querySelector("#mode-url").click();
    assert.equal(document.querySelector("#geolocation-form").hidden, false);
    document.querySelector("#geolocation-form").dispatchEvent(new window.Event("submit", { cancelable: true }));
    assert.equal(document.querySelector("#url-error").hidden, false);
    document.querySelector("#settings-toggle").click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(document.querySelector("#connection-settings").open, true);
    assert.equal(connectionScrolled, true);
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.document;
    delete globalThis.window;
  }
});

test("charge puis efface le jeton mémorisé sans l’envoyer à la sonde de santé", async () => {
  const { document, window } = parseHTML(await readFile(new URL("../index.html", import.meta.url), "utf8"));
  const apiBase = "https://api.example";
  const values = new Map([[API_TOKEN_STORAGE_KEY, JSON.stringify({
    origin: apiBase,
    token: "persisted-test-token",
  })]]);
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  const localStorageDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
  const originalFetch = globalThis.fetch;
  const originalConfig = globalThis.NETRYX_CONFIG;
  let healthRequest;
  globalThis.document = document;
  globalThis.window = window;
  globalThis.NETRYX_CONFIG = { apiBase };
  Object.defineProperty(window, "localStorage", { configurable: true, value: storage });
  globalThis.fetch = async (url, options) => {
    healthRequest = { url, options };
    return new Response(JSON.stringify({ status: "ok", auth_required: true }), {
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await import(`../js/main.js?persisted-token=${Date.now()}`);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(document.querySelector("#connection-feedback").textContent, "Jeton mémorisé chargé pour ce navigateur.");
    assert.equal(healthRequest.url.includes("persisted-test-token"), false);
    assert.equal(healthRequest.options.headers.Authorization, undefined);

    document.querySelector("#clear-token").click();
    assert.equal(values.has(API_TOKEN_STORAGE_KEY), false);
    assert.equal(document.querySelector("#connection-feedback").textContent, "Jeton effacé de ce navigateur.");
  } finally {
    globalThis.fetch = originalFetch;
    if (originalConfig === undefined) delete globalThis.NETRYX_CONFIG;
    else globalThis.NETRYX_CONFIG = originalConfig;
    if (localStorageDescriptor) Object.defineProperty(window, "localStorage", localStorageDescriptor);
    else delete window.localStorage;
    delete globalThis.document;
    delete globalThis.window;
  }
});
