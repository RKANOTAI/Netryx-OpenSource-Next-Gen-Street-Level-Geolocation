import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseHTML } from "linkedom";

// DOM wiring test: pure state tests do not catch a renamed form reference.
test("la vraie page démarre, sonde le moteur et valide les deux formulaires", async () => {
  const { document, window } = parseHTML(await readFile(new URL("../index.html", import.meta.url), "utf8"));
  const originalFetch = globalThis.fetch;
  globalThis.document = document;
  globalThis.window = window;
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
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.document;
    delete globalThis.window;
  }
});
