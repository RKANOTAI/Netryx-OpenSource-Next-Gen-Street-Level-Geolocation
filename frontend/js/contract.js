import { safeMapsUrl, validCoordinates } from "./formatters.js";

const STATUSES = new Set(["queued", "running", "succeeded", "not_found", "failed", "blocked"]);
const CONFIDENCE = new Set(["HIGH", "MEDIUM", "LOW", "NONE"]);

function object(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} invalide`);
  return value;
}

function normalizeProgress(value) {
  if (value == null) return null;
  const progress = object(value, "progression");
  const percent = progress.percent == null ? null : Number(progress.percent);
  if (percent != null && (!Number.isFinite(percent) || percent < 0 || percent > 100)) {
    throw new TypeError("pourcentage invalide");
  }
  return {
    phase: typeof progress.phase === "string" ? progress.phase : "queued",
    percent,
    completed: progress.completed ?? null,
    total: progress.total ?? null,
    message_fr: typeof progress.message_fr === "string" ? progress.message_fr : "",
    updated_at: progress.updated_at ?? null,
  };
}

function safePanoramaxUrl(candidate) {
  try {
    const url = new URL(candidate);
    if (url.protocol !== "https:" || !url.hostname.toLowerCase().includes("panoramax")) return null;
    return url.href;
  } catch {
    return null;
  }
}

function normalizeSources(result) {
  const raw = Array.isArray(result.sources)
    ? result.sources
    : Array.isArray(result.panoramax_sources) ? result.panoramax_sources : [];
  return raw.flatMap((entry) => {
    const url = typeof entry === "string" ? entry : entry?.url;
    const safeUrl = safePanoramaxUrl(url);
    return safeUrl ? [{ label_fr: typeof entry?.label_fr === "string" ? entry.label_fr : "Source Panoramax", url: safeUrl }] : [];
  });
}

function normalizeResult(value) {
  const result = object(value, "résultat");
  const location = object(result.location, "localisation");
  const latitude = Number(location.latitude);
  const longitude = Number(location.longitude);
  if (!validCoordinates(latitude, longitude)) throw new TypeError("coordonnées invalides");
  const confidence = object(result.confidence, "confiance");
  const level = String(confidence.level ?? "NONE").toUpperCase();
  if (!CONFIDENCE.has(level)) throw new TypeError("niveau de confiance invalide");
  if (!Array.isArray(result.evidence)) throw new TypeError("preuves invalides");
  const evidence = result.evidence.map((entry) => {
    object(entry, "preuve");
    if (typeof entry.label_fr !== "string" || typeof entry.detail_fr !== "string") {
      throw new TypeError("preuve incomplète");
    }
    return {
      kind: typeof entry.kind === "string" ? entry.kind : "evidence",
      label_fr: entry.label_fr,
      detail_fr: entry.detail_fr,
      value: entry.value ?? null,
      unit: entry.unit ?? null,
    };
  });
  return {
    summary_fr: typeof result.summary_fr === "string" ? result.summary_fr : "",
    location: {
      label_fr: typeof location.label_fr === "string" ? location.label_fr : null,
      latitude,
      longitude,
    },
    confidence: { level, score: confidence.score ?? null },
    evidence,
    google_maps_url: safeMapsUrl(result.google_maps_url, latitude, longitude),
    panoramax_url: safePanoramaxUrl(result.panoramax_url),
    sources: normalizeSources(result),
  };
}

export function normalizeJobSnapshot(value) {
  const snapshot = object(value, "tâche");
  if (typeof snapshot.job_id !== "string" || !snapshot.job_id) throw new TypeError("identifiant invalide");
  if (!STATUSES.has(snapshot.status)) throw new TypeError("statut invalide");
  const result = snapshot.status === "succeeded" ? normalizeResult(snapshot.result) : null;
  return {
    job_id: snapshot.job_id,
    status: snapshot.status,
    progress: normalizeProgress(snapshot.progress),
    result,
    error: snapshot.error && typeof snapshot.error === "object" ? {
      code: String(snapshot.error.code ?? "UNKNOWN_ERROR"),
      message_fr: String(snapshot.error.message_fr ?? "L’analyse a échoué."),
      retryable: Boolean(snapshot.error.retryable),
    } : null,
  };
}
