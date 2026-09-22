import { confidenceLabel, coordinatesLabel, PHASE_LABELS } from "./formatters.js";

const PHASES = [
  "queued",
  "preparing_photo_manifest",
  "uploading",
  "validating",
  "fetching_listing",
  "extracting_query",
  "searching_index",
  "extracting_candidates",
  "matching",
  "refining",
  "checking_syndication",
  "complete",
];

function phaseIndex(phase) {
  if (phase === "extracting_candidates") return PHASES.indexOf("matching");
  return PHASES.indexOf(phase);
}

function makeText(document, tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

export function createView(document) {
  const elements = {
    photoForm: document.querySelector("#photo-form"),
    urlForm: document.querySelector("#geolocation-form"),
    photoMode: document.querySelector("#mode-photos"),
    urlMode: document.querySelector("#mode-url"),
    photoInput: document.querySelector("#photo-files"),
    photoPreviews: document.querySelector("#photo-previews"),
    photoFilesError: document.querySelector("#photo-files-error"),
    photoError: document.querySelector("#photo-error"),
    photoSubmit: document.querySelector("#photo-submit-button"),
    latitude: document.querySelector("#photo-latitude"),
    longitude: document.querySelector("#photo-longitude"),
    radius: document.querySelector("#photo-radius"),
    reviewedExterior: document.querySelector("#reviewed-exterior"),
    imageSize: document.querySelector("#image-size"),
    settingsToggle: document.querySelector("#settings-toggle"),
    connectionSettings: document.querySelector("#connection-settings"),
    connectionForm: document.querySelector("#connection-form"),
    apiOrigin: document.querySelector("#api-origin"),
    clearApiOverride: document.querySelector("#clear-api-override"),
    apiOriginError: document.querySelector("#api-origin-error"),
    apiToken: document.querySelector("#api-token"),
    saveToken: document.querySelector("#save-token"),
    clearToken: document.querySelector("#clear-token"),
    authHelp: document.querySelector("#auth-help"),
    connectionFeedback: document.querySelector("#connection-feedback"),
    connectionDescription: document.querySelector("#connection-description"),
    input: document.querySelector("#listing-url"),
    submit: document.querySelector("#submit-button"),
    urlError: document.querySelector("#url-error"),
    progressPanel: document.querySelector("#progress-panel"),
    progressTitle: document.querySelector("#progress-title"),
    progressPercent: document.querySelector("#progress-percent"),
    progressMessage: document.querySelector("#progress-message"),
    progressBar: document.querySelector("#progress-bar"),
    phaseList: document.querySelector("#phase-list"),
    empty: document.querySelector("#empty-state"),
    result: document.querySelector("#result-panel"),
    resultTitle: document.querySelector("#result-title"),
    resultStatus: document.querySelector("#result-status"),
    location: document.querySelector("#location-label"),
    summary: document.querySelector("#result-summary"),
    confidence: document.querySelector("#confidence-value"),
    coordinates: document.querySelector("#coordinates-value"),
    mapsLink: document.querySelector("#maps-link"),
    panoramaxLink: document.querySelector("#panoramax-link"),
    sourceList: document.querySelector("#source-list"),
    evidence: document.querySelector("#evidence-list"),
    notFound: document.querySelector("#not-found-panel"),
    error: document.querySelector("#error-panel"),
    errorTitle: document.querySelector("#error-title"),
    errorMessage: document.querySelector("#error-message"),
  };

  for (const phase of PHASES) {
    const item = document.createElement("li");
    item.className = "phase-item";
    item.dataset.phase = phase;
    const node = document.createElement("span");
    node.className = "phase-node";
    node.setAttribute("aria-hidden", "true");
    const label = makeText(document, "span", "", PHASE_LABELS[phase] ?? phase);
    item.append(node, label);
    elements.phaseList.append(item);
  }

  function renderPhases(activePhase, status) {
    const active = phaseIndex(activePhase);
    for (const item of elements.phaseList.children) {
      const index = phaseIndex(item.dataset.phase);
      item.classList.toggle("done", index >= 0 && active >= 0 && index < active);
      item.classList.toggle("active", index === active && !["failed", "blocked", "not_found"].includes(status));
      if (item.classList.contains("active")) item.setAttribute("aria-current", "step");
      else item.removeAttribute("aria-current");
    }
  }

  function renderPhotoPreviews(photos) {
    elements.photoPreviews.replaceChildren();
    for (const photo of photos) {
      const item = document.createElement("li");
      item.className = "photo-preview";
      item.dataset.photoId = photo.id;
      if (photo.previewUrl) {
        const image = document.createElement("img");
        image.src = photo.previewUrl;
        image.alt = `Aperçu de ${photo.file?.name ?? "la photo"}`;
        item.append(image);
      } else {
        item.append(makeText(document, "span", "photo-preview-placeholder", "IMG"));
      }
      const details = makeText(document, "span", "photo-preview-details", photo.file?.name ?? "Photo");
      if (photo.error) details.append(makeText(document, "small", "photo-preview-error", photo.error));
      const remove = makeText(document, "button", "photo-remove", "Retirer");
      remove.type = "button";
      remove.dataset.removePhoto = photo.id;
      remove.setAttribute("aria-label", `Retirer ${photo.file?.name ?? "la photo"}`);
      item.append(details, remove);
      elements.photoPreviews.append(item);
    }
  }

  function renderResult(result) {
    const { location, confidence, evidence } = result;
    const coordinates = coordinatesLabel(location.latitude, location.longitude);
    elements.location.textContent = location.label_fr || coordinates;
    elements.summary.textContent = result.summary_fr || "Le moteur fournit une estimation à partir des indices disponibles.";
    elements.confidence.textContent = confidenceLabel(confidence.level);
    elements.coordinates.textContent = coordinates;
    elements.mapsLink.href = result.google_maps_url;
    elements.mapsLink.setAttribute("aria-label", "Ouvrir la position estimée dans Google Maps, nouvel onglet");

    elements.panoramaxLink.hidden = !result.panoramax_url;
    if (result.panoramax_url) elements.panoramaxLink.href = result.panoramax_url;
    elements.sourceList.replaceChildren();
    for (const source of result.sources ?? []) {
      const item = document.createElement("li");
      const link = makeText(document, "a", "source-link", `${source.label_fr} ↗`);
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      item.append(link);
      elements.sourceList.append(item);
    }

    elements.evidence.replaceChildren();
    for (const entry of evidence) {
      const item = document.createElement("li");
      item.className = "evidence-item";
      item.append(makeText(document, "strong", "evidence-label", entry.label_fr));
      item.append(makeText(document, "p", "evidence-detail", entry.detail_fr));
      elements.evidence.append(item);
    }
  }

  return {
    elements,
    render(state) {
      const busy = ["submitting", "queued", "running"].includes(state.phase);
      elements.photoForm.hidden = state.mode !== "photos";
      elements.urlForm.hidden = state.mode !== "url";
      elements.photoMode.classList.toggle("is-active", state.mode === "photos");
      elements.urlMode.classList.toggle("is-active", state.mode === "url");
      elements.photoMode.setAttribute("aria-selected", String(state.mode === "photos"));
      elements.urlMode.setAttribute("aria-selected", String(state.mode === "url"));
      elements.submit.disabled = busy;
      elements.photoSubmit.disabled = busy;
      elements.reviewedExterior.checked = state.reviewedExterior;
      elements.submit.textContent = busy ? "Analyse en cours…" : "Lancer l’analyse";
      elements.photoSubmit.textContent = busy ? "Analyse en cours…" : "Lancer l’analyse photo";
      elements.progressPanel.setAttribute("aria-busy", String(busy));
      renderPhotoPreviews(state.photos);
      elements.latitude.value = state.region?.latitude ?? "";
      elements.longitude.value = state.region?.longitude ?? "";
      elements.radius.value = state.region?.radiusM ?? "";
      elements.reviewedExterior.checked = Boolean(state.reviewedExterior);

      const invalid = state.phase === "invalid";
      const invalidField = state.error?.field;
      elements.urlError.hidden = !invalid || (invalidField && invalidField !== "url");
      elements.urlError.textContent = !elements.urlError.hidden ? state.error?.message_fr ?? "URL invalide." : "";
      elements.photoError.hidden = !invalid || (invalidField && invalidField !== "photos");
      elements.photoError.textContent = !elements.photoError.hidden ? state.error?.message_fr ?? "Paramètres photo invalides." : "";
      elements.input.setAttribute("aria-invalid", String(!elements.urlError.hidden));
      elements.photoFilesError.hidden = !state.photoSelectionError;
      elements.photoFilesError.textContent = state.photoSelectionError ?? "";

      const progress = state.progress;
      renderPhases(progress?.phase ?? "queued", state.phase);
      elements.progressMessage.textContent = progress?.message_fr || (state.phase === "idle" ? "En attente d’une recherche." : "Analyse en cours…");
      if (Number.isFinite(progress?.percent)) {
        elements.progressBar.value = progress.percent;
        elements.progressPercent.textContent = `${Math.round(progress.percent)}%`;
      } else {
        elements.progressBar.removeAttribute("value");
        elements.progressPercent.textContent = busy ? "Actif" : "Prêt";
      }

      const succeeded = state.phase === "succeeded" && state.result;
      const notFound = state.phase === "not_found";
      const failed = ["failed", "blocked"].includes(state.phase);
      elements.empty.hidden = Boolean(succeeded || notFound || failed);
      elements.result.hidden = !succeeded;
      elements.notFound.hidden = !notFound;
      elements.error.hidden = !failed;

      if (succeeded) {
        renderResult(state.result);
        elements.resultStatus.textContent = "Position de caméra estimée";
      } else if (notFound) {
        elements.resultStatus.textContent = "Non trouvé";
      } else if (failed) {
        elements.resultStatus.textContent = state.phase === "blocked" ? "Accès bloqué" : "Erreur";
        elements.errorMessage.textContent = state.error?.message_fr ?? "L’analyse a échoué.";
      } else if (busy) {
        elements.resultStatus.textContent = "Analyse active";
      } else {
        elements.resultStatus.textContent = "Aucune analyse";
      }
    },
  };
}
