import { normalizeJobSnapshot } from "./contract.js";

const TERMINAL = new Set(["succeeded", "not_found", "failed", "blocked"]);
const DEFAULT_TIMEOUT_MS = 20_000;

function endpoint(apiBase, path) {
  const base = String(apiBase ?? "").replace(/\/$/, "");
  return `${base}${path}`;
}

function authHeaders(token) {
  const headers = { Accept: "application/json" };
  const value = String(token ?? "").trim();
  if (value) headers.Authorization = `Bearer ${value}`;
  return headers;
}

function requestContext(signal, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  let timer = null;
  const forwardAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", forwardAbort, { once: true });
  }
  if (timeoutMs > 0) timer = setTimeout(() => controller.abort(), timeoutMs);
  return {
    signal: controller.signal,
    cleanup() {
      if (timer) clearTimeout(timer);
      signal?.removeEventListener("abort", forwardAbort);
    },
  };
}

async function jsonResponse(response) {
  const type = response.headers?.get?.("content-type") ?? "";
  let payload = null;
  if (type.includes("application/json")) {
    payload = await response.json();
  } else if (response.ok) {
    throw new Error("Réponse JSON attendue.");
  }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = detail?.message_fr ?? payload?.message_fr ?? "Le service de recherche est indisponible.";
    const error = new Error(message);
    error.status = response.status;
    error.code = detail?.code ?? payload?.code;
    throw error;
  }
  return payload;
}

async function fetchJson(url, options = {}) {
  const fetchImpl = options.fetchImpl ?? fetch;
  const context = requestContext(options.signal, options.timeoutMs);
  try {
    return await fetchImpl(url, { ...options, signal: context.signal, fetchImpl: undefined, timeoutMs: undefined });
  } finally {
    context.cleanup();
  }
}

function acceptedPayload(payload) {
  if (!payload || typeof payload.job_id !== "string" || !payload.job_id || payload.status !== "queued") {
    throw new Error("Le service a renvoyé une tâche invalide.");
  }
  return payload;
}

export async function createGeolocationJob(listingUrl, options = {}) {
  const response = await fetchJson(endpoint(options.apiBase, "/api/v1/geolocations"), {
    method: "POST",
    headers: { ...authHeaders(options.token ?? options.authToken), "Content-Type": "application/json" },
    body: JSON.stringify({ listing_url: listingUrl }),
    signal: options.signal,
    timeoutMs: options.timeoutMs,
    fetchImpl: options.fetchImpl,
  });
  return acceptedPayload(await jsonResponse(response));
}

function fileName(photo, index) {
  const name = String(photo?.name ?? "").trim();
  return name || `photo-${index + 1}.jpg`;
}

export async function createPhotoGeolocationJob(payload = {}, options = {}) {
  const photos = payload.photos ?? payload.files ?? [];
  const latitude = payload.latitude;
  const longitude = payload.longitude;
  const radiusM = payload.radiusM ?? payload.radius_m;
  const reviewedExterior = payload.reviewedExterior ?? payload.reviewed_exterior;
  const imageSize = payload.imageSize ?? payload.image_size ?? "hd";
  const formData = new FormData();
  for (const [index, photo] of Array.from(photos).entries()) {
    formData.append("photos", photo, fileName(photo, index));
  }
  formData.append("latitude", String(latitude));
  formData.append("longitude", String(longitude));
  formData.append("radius_m", String(radiusM));
  formData.append("reviewed_exterior", String(reviewedExterior));
  formData.append("image_size", imageSize);

  const response = await fetchJson(endpoint(options.apiBase, "/api/v1/photo-geolocations"), {
    method: "POST",
    headers: authHeaders(options.token ?? options.authToken),
    body: formData,
    signal: options.signal,
    timeoutMs: options.timeoutMs,
    fetchImpl: options.fetchImpl,
  });
  return acceptedPayload(await jsonResponse(response));
}

export async function getGeolocationJob(jobId, options = {}) {
  const encoded = encodeURIComponent(jobId);
  const response = await fetchJson(endpoint(options.apiBase, `/api/v1/geolocations/${encoded}`), {
    headers: authHeaders(options.token ?? options.authToken),
    signal: options.signal,
    timeoutMs: options.timeoutMs,
    fetchImpl: options.fetchImpl,
  });
  return normalizeJobSnapshot(await jsonResponse(response));
}

export async function checkHealth(apiBase, options = {}) {
  const response = await fetchJson(endpoint(apiBase, "/healthz"), {
    headers: { Accept: "application/json" },
    signal: options.signal,
    timeoutMs: options.timeoutMs ?? 8_000,
    fetchImpl: options.fetchImpl,
  });
  const payload = await jsonResponse(response);
  if (payload?.status !== "ok") throw new Error("Le service de recherche a renvoyé un état invalide.");
  return { status: "ok", auth_required: Boolean(payload.auth_required) };
}

function defaultDelay(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(resolve, milliseconds);
    if (!signal) return;
    const abort = () => {
      clearTimeout(timeout);
      reject(new DOMException("Aborted", "AbortError"));
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

export async function watchGeolocationJob(jobId, options = {}) {
  const delay = options.delay ?? defaultDelay;
  const pollAfterMs = Math.max(100, Number(options.pollAfterMs ?? 1500));
  while (true) {
    if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const snapshot = await getGeolocationJob(jobId, options);
    options.onUpdate?.(snapshot);
    if (TERMINAL.has(snapshot.status)) return snapshot;
    await delay(pollAfterMs, options.signal);
  }
}
