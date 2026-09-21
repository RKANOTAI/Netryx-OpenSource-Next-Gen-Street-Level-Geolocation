import { checkHealth, createGeolocationJob, createPhotoGeolocationJob, watchGeolocationJob } from "./api.js";
import {
  clearApiBaseOverride,
  clearSessionApiToken,
  getApiBase,
  getPollAfterMs,
  getSessionApiToken,
  isGithubPages,
  setApiBaseOverride,
  setSessionApiToken,
} from "./config.js";
import { initialState, reduce } from "./state.js";
import { validateListingUrl, validatePhotoSubmission } from "./validation.js";
import { createPreviewRegistry } from "./previews.js";
import { createView } from "./view.js";

const view = createView(document);
const previews = createPreviewRegistry();
let state = { ...initialState };
let activeController = null;
let runSequence = 0;

function dispatch(action) {
  state = reduce(state, action);
  view.render(state);
}

function photoId(file, index) {
  return `${file.name ?? "photo"}:${file.size ?? 0}:${file.lastModified ?? 0}:${index}`;
}

function photoValidationError(file) {
  const result = validatePhotoSubmission({
    photos: [file],
    latitude: 0,
    longitude: 0,
    radiusM: 50,
    reviewedExterior: true,
    imageSize: "hd",
  });
  return result.valid ? null : result.message;
}

function selectPhotos(fileList) {
  previews.cleanup();
  const selected = Array.from(fileList ?? []);
  const limited = selected.slice(0, 4);
  const error = selected.length > 4 ? "Sélectionnez au maximum 4 photos." : null;
  const photos = limited.map((file, index) => ({
    id: photoId(file, index),
    file,
    previewUrl: previews.add(file),
    error: photoValidationError(file),
  }));
  dispatch({ type: "PHOTOS_SET", photos, error });
}

function apiOptions(controller) {
  return {
    apiBase: getApiBase(),
    token: getSessionApiToken(),
    signal: controller.signal,
  };
}

function showConnectionSettings() {
  view.elements.connectionSettings.open = true;
  view.elements.settingsToggle.setAttribute("aria-expanded", "true");
  view.elements.connectionSettings.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  view.elements.apiOrigin.focus({ preventScroll: true });
}

function setApiStatus(kind, text) {
  const dot = document.querySelector("#api-status-dot");
  const statusText = document.querySelector("#api-status-text");
  dot.classList.remove("online", "offline");
  if (kind) dot.classList.add(kind);
  statusText.textContent = text;
}

async function probeApi() {
  const apiBase = getApiBase();
  if (!apiBase && isGithubPages()) {
    setApiStatus("offline", "Moteur à connecter");
    view.elements.connectionSettings.open = true;
    view.elements.settingsToggle.setAttribute("aria-expanded", "true");
    view.elements.connectionDescription.textContent = "Cette page GitHub Pages n’a pas d’API même origine. Saisissez l’origine HTTPS publique de votre moteur.";
    return;
  }
  view.elements.connectionDescription.textContent = apiBase
    ? "Origine API active. L’override local est conservé dans ce navigateur, jamais dans l’URL."
    : "En local, une origine vide utilise le moteur servi sur la même origine.";
  try {
    const health = await checkHealth(apiBase);
    if (health.auth_required) {
      setApiStatus("online", "Authentification requise");
      view.elements.authHelp.hidden = false;
      view.elements.authHelp.textContent = "Le moteur répond mais demande un jeton. Saisissez-le ci-dessous ; il restera uniquement en mémoire pendant cette session.";
      view.elements.connectionSettings.open = true;
      view.elements.settingsToggle.setAttribute("aria-expanded", "true");
    } else {
      setApiStatus("online", "Moteur en ligne");
      view.elements.authHelp.hidden = true;
    }
  } catch {
    setApiStatus("offline", apiBase ? "Moteur hors ligne" : "Moteur à connecter");
  }
}

function photoPayload() {
  return {
    photos: state.photos.map((photo) => photo.file),
    latitude: view.elements.latitude.value,
    longitude: view.elements.longitude.value,
    radiusM: view.elements.radius.value,
    reviewedExterior: view.elements.reviewedExterior.checked,
    imageSize: view.elements.imageSize.value,
  };
}

async function runSearch(mode) {
  const listingValidation = mode === "url" ? validateListingUrl(view.elements.input.value) : null;
  const photoValidation = mode === "photos" ? validatePhotoSubmission(photoPayload()) : null;
  const validation = listingValidation ?? photoValidation;
  if (!validation.valid) {
    dispatch({ type: "SUBMIT_INVALID", field: mode, message: validation.message });
    if (mode === "url") view.elements.input.focus();
    else if (!photoValidation?.message?.toLowerCase().includes("photo")) view.elements.latitude.focus();
    return;
  }

  activeController?.abort();
  activeController = new AbortController();
  const runId = ++runSequence;
  dispatch({
    type: "SUBMIT_START",
    runId,
    listingUrl: mode === "url" ? validation.value : undefined,
  });

  try {
    const options = apiOptions(activeController);
    if (!options.apiBase && isGithubPages()) {
      throw new Error("Aucune API n’est configurée pour cette page. Ouvrez Connexion et saisissez une origine HTTPS.");
    }
    const accepted = mode === "url"
      ? await createGeolocationJob(validation.value, options)
      : await createPhotoGeolocationJob(validation.value, options);
    if (runId !== runSequence) return;
    dispatch({ type: "JOB_ACCEPTED", runId, accepted });
    view.elements.progressTitle.focus({ preventScroll: true });
    await watchGeolocationJob(accepted.job_id, {
      ...options,
      pollAfterMs: accepted.poll_after_ms ?? getPollAfterMs(),
      onUpdate(snapshot) {
        if (runId === runSequence) dispatch({ type: "JOB_UPDATED", runId, snapshot });
      },
    });
    if (runId !== runSequence) return;
    if (state.phase === "succeeded") view.elements.resultTitle.focus({ preventScroll: true });
    else if (["failed", "blocked"].includes(state.phase)) view.elements.errorTitle.focus({ preventScroll: true });
  } catch (error) {
    if (runId !== runSequence || error?.name === "AbortError") return;
    dispatch({ type: "REQUEST_FAILED", runId, message: error?.message || "Connexion au service de recherche impossible." });
    view.elements.errorTitle.focus({ preventScroll: true });
  } finally {
    if (runId === runSequence) activeController = null;
  }
}

view.elements.photoMode.addEventListener("click", () => dispatch({ type: "SET_MODE", mode: "photos" }));
view.elements.urlMode.addEventListener("click", () => dispatch({ type: "SET_MODE", mode: "url" }));
view.elements.photoInput.addEventListener("change", () => selectPhotos(view.elements.photoInput.files));
view.elements.photoPreviews.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-photo]");
  if (!button) return;
  const id = button.dataset.removePhoto;
  const photo = state.photos.find((item) => item.id === id);
  previews.remove(photo?.previewUrl);
  dispatch({ type: "PHOTO_REMOVE", id });
});
view.elements.photoForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch("photos");
});
view.elements.urlForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch("url");
});

for (const field of [view.elements.latitude, view.elements.longitude, view.elements.radius]) {
  field.addEventListener("input", () => {
    dispatch({ type: "SET_REGION", region: {
      latitude: view.elements.latitude.value,
      longitude: view.elements.longitude.value,
      radiusM: view.elements.radius.value,
    } });
  });
}
view.elements.reviewedExterior.addEventListener("change", () => dispatch({ type: "SET_REVIEWED_EXTERIOR", value: view.elements.reviewedExterior.checked }));
view.elements.imageSize.addEventListener("change", () => dispatch({ type: "SET_IMAGE_SIZE", value: view.elements.imageSize.value }));
view.elements.input.addEventListener("input", () => {
  if (state.phase === "invalid") dispatch({ type: "RESET" });
});

for (const button of document.querySelectorAll(".retry-button")) {
  button.addEventListener("click", () => {
    dispatch({ type: "RESET" });
    if (state.mode === "url") view.elements.input.focus();
    else view.elements.photoInput.focus();
  });
}

view.elements.settingsToggle.addEventListener("click", () => {
  const open = !view.elements.connectionSettings.open;
  view.elements.connectionSettings.open = open;
  view.elements.settingsToggle.setAttribute("aria-expanded", String(open));
  if (open) view.elements.apiOrigin.focus({ preventScroll: true });
});
view.elements.connectionSettings.addEventListener("toggle", () => {
  view.elements.settingsToggle.setAttribute("aria-expanded", String(view.elements.connectionSettings.open));
});
view.elements.connectionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    const value = setApiBaseOverride(view.elements.apiOrigin.value);
    view.elements.apiOrigin.value = value;
    view.elements.apiOriginError.hidden = true;
    view.elements.connectionFeedback.textContent = "Origine enregistrée. Test de connexion en cours…";
    probeApi().finally(() => {
      if (document.querySelector("#api-status-dot")?.classList.contains("online")) view.elements.connectionFeedback.textContent = "Connexion vérifiée.";
    });
  } catch (error) {
    view.elements.apiOriginError.hidden = false;
    view.elements.apiOriginError.textContent = error.message;
    view.elements.apiOrigin.focus();
  }
});
view.elements.clearApiOverride.addEventListener("click", () => {
  clearApiBaseOverride();
  view.elements.apiOrigin.value = getApiBase();
  view.elements.connectionFeedback.textContent = "Origine réinitialisée.";
  probeApi();
});
view.elements.saveToken.addEventListener("click", () => {
  setSessionApiToken(view.elements.apiToken.value);
  view.elements.apiToken.value = "";
  view.elements.connectionFeedback.textContent = "Jeton utilisé pour cette session uniquement.";
  probeApi();
});
view.elements.clearToken.addEventListener("click", () => {
  clearSessionApiToken();
  view.elements.apiToken.value = "";
  view.elements.connectionFeedback.textContent = "Jeton effacé de la session.";
});

view.elements.apiOrigin.value = getApiBase();
view.render(state);
probeApi();
window.addEventListener("beforeunload", () => {
  activeController?.abort();
  previews.cleanup();
}, { once: true });
