import test from "node:test";
import assert from "node:assert/strict";

import { createPreviewRegistry } from "../js/previews.js";

test("révoque chaque URL de prévisualisation retirée ou abandonnée", () => {
  const revoked = [];
  const registry = createPreviewRegistry({
    createObjectURL: (file) => `blob:${file.name}`,
    revokeObjectURL: (url) => revoked.push(url),
  });
  const first = registry.add({ name: "one.jpg" });
  registry.add({ name: "two.jpg" });
  registry.remove(first);
  registry.cleanup();
  assert.deepEqual(revoked, ["blob:one.jpg", "blob:two.jpg"]);
});
