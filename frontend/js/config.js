import { validateApiOrigin } from "./validation.js";

export const API_BASE_OVERRIDE_KEY = "netryx.apiBaseOverride";
let sessionApiToken = "";

function defaultConfig() {
  return globalThis.NETRYX_CONFIG && typeof globalThis.NETRYX_CONFIG === "object"
    ? globalThis.NETRYX_CONFIG
    : {};
}

function browserStorage(storage) {
  if (storage !== undefined) return storage;
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

function cleanBase(value) {
  return String(value ?? "").trim().replace(/\/$/, "");
}

export function getApiBase(options = {}) {
  const storage = browserStorage(options.storage);
  const override = storage?.getItem?.(API_BASE_OVERRIDE_KEY);
  if (override !== null && override !== undefined) {
    const valid = validateApiOrigin(override);
    if (valid.valid) return cleanBase(valid.value);
  }
  return cleanBase(options.config?.apiBase ?? defaultConfig().apiBase ?? "");
}

export function setApiBaseOverride(input, storage) {
  const valid = validateApiOrigin(input);
  if (!valid.valid) throw new TypeError(valid.message);
  const value = cleanBase(valid.value);
  const target = browserStorage(storage);
  if (target?.setItem) {
    if (value) target.setItem(API_BASE_OVERRIDE_KEY, value);
    else target.removeItem?.(API_BASE_OVERRIDE_KEY);
  }
  return value;
}

export function clearApiBaseOverride(storage) {
  browserStorage(storage)?.removeItem?.(API_BASE_OVERRIDE_KEY);
}

export function getPollAfterMs(config = defaultConfig()) {
  const value = Number(config.pollAfterMs ?? 1000);
  return Number.isFinite(value) && value > 0 ? value : 1000;
}

export function isGithubPages(locationLike = globalThis.location) {
  const hostname = String(locationLike?.hostname ?? "").toLowerCase();
  return hostname.endsWith(".github.io") || hostname === "github.io";
}

export function setSessionApiToken(value) {
  sessionApiToken = String(value ?? "").trim();
}

export function getSessionApiToken() {
  return sessionApiToken;
}

export function clearSessionApiToken() {
  sessionApiToken = "";
}
