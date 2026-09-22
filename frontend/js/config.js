import { validateApiOrigin } from "./validation.js";

export const API_BASE_OVERRIDE_KEY = "netryx.apiBaseOverride";
export const API_TOKEN_STORAGE_KEY = "netryx.apiToken";
let sessionApiToken = "";
let sessionApiTokenOrigin = "";

function defaultConfig() {
  return globalThis.NETRYX_CONFIG && typeof globalThis.NETRYX_CONFIG === "object"
    ? globalThis.NETRYX_CONFIG
    : {};
}

function browserStorage(storage) {
  if (storage !== undefined) return storage;
  if (typeof window === "undefined") return null;
  const descriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
  if (window.constructor?.name !== "Window" && !Object.hasOwn(descriptor ?? {}, "value")) return null;
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}

function storageGet(storage, key) {
  try {
    return storage?.getItem?.(key) ?? null;
  } catch {
    return null;
  }
}

function storageSet(storage, key, value) {
  try {
    storage?.setItem?.(key, value);
  } catch {
    // Keep the in-memory fallback when browser storage is unavailable.
  }
}

function storageRemove(storage, key) {
  try {
    storage?.removeItem?.(key);
  } catch {
    // Clearing the in-memory value remains authoritative for this page.
  }
}

function cleanBase(value) {
  return String(value ?? "").trim().replace(/\/$/, "");
}

export function getApiBase(options = {}) {
  const storage = browserStorage(options.storage);
  const override = storageGet(storage, API_BASE_OVERRIDE_KEY);
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
  if (value) storageSet(target, API_BASE_OVERRIDE_KEY, value);
  else storageRemove(target, API_BASE_OVERRIDE_KEY);
  return value;
}

export function clearApiBaseOverride(storage) {
  storageRemove(browserStorage(storage), API_BASE_OVERRIDE_KEY);
}

export function getPollAfterMs(config = defaultConfig()) {
  const value = Number(config.pollAfterMs ?? 1000);
  return Number.isFinite(value) && value > 0 ? value : 1000;
}

export function isGithubPages(locationLike = globalThis.location) {
  const hostname = String(locationLike?.hostname ?? "").toLowerCase();
  return hostname.endsWith(".github.io") || hostname === "github.io";
}

export function setSessionApiToken(value, storage) {
  const target = browserStorage(storage);
  sessionApiToken = String(value ?? "").trim();
  sessionApiTokenOrigin = getApiBase({ storage: target });
  if (sessionApiToken) {
    storageSet(target, API_TOKEN_STORAGE_KEY, JSON.stringify({
      origin: sessionApiTokenOrigin,
      token: sessionApiToken,
    }));
  } else {
    storageRemove(target, API_TOKEN_STORAGE_KEY);
  }
}

export function getSessionApiToken(storage) {
  const target = browserStorage(storage);
  const origin = getApiBase({ storage: target });
  if (sessionApiToken && sessionApiTokenOrigin === origin) return sessionApiToken;
  sessionApiToken = "";
  sessionApiTokenOrigin = "";
  const stored = storageGet(target, API_TOKEN_STORAGE_KEY);
  if (!stored) return "";
  try {
    const parsed = JSON.parse(stored);
    if (
      !parsed
      || typeof parsed !== "object"
      || parsed.origin !== origin
      || typeof parsed.token !== "string"
    ) return "";
    sessionApiToken = parsed.token.trim();
    sessionApiTokenOrigin = origin;
  } catch {
    storageRemove(target, API_TOKEN_STORAGE_KEY);
  }
  return sessionApiToken;
}

export function clearSessionApiToken(storage) {
  sessionApiToken = "";
  sessionApiTokenOrigin = "";
  storageRemove(browserStorage(storage), API_TOKEN_STORAGE_KEY);
}
