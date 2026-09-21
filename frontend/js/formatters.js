const CONFIDENCE_LABELS = {
  HIGH: "Élevée",
  MEDIUM: "Moyenne",
  LOW: "Faible",
  NONE: "Non disponible",
};

export const PHASE_LABELS = {
  queued: "En attente",
  uploading: "Envoi des photos",
  validating: "Validation des paramètres",
  fetching_listing: "Récupération de l’annonce",
  extracting_query: "Analyse des images",
  searching_index: "Recherche dans l’index",
  extracting_candidates: "Téléchargement des vues candidates",
  matching: "Comparaison géométrique",
  refining: "Raffinement de la position",
  complete: "Analyse terminée",
  failed: "Analyse interrompue",
};

export function confidenceLabel(level) {
  return CONFIDENCE_LABELS[level] ?? CONFIDENCE_LABELS.NONE;
}

export function coordinatesLabel(latitude, longitude) {
  if (!validCoordinates(latitude, longitude)) return "Coordonnées indisponibles";
  return `${Number(latitude).toFixed(6)}, ${Number(longitude).toFixed(6)}`;
}

export function validCoordinates(latitude, longitude) {
  return Number.isFinite(Number(latitude))
    && Number.isFinite(Number(longitude))
    && Number(latitude) >= -90
    && Number(latitude) <= 90
    && Number(longitude) >= -180
    && Number(longitude) <= 180;
}

export function googleMapsUrl(latitude, longitude) {
  if (!validCoordinates(latitude, longitude)) return null;
  const query = encodeURIComponent(`${Number(latitude)},${Number(longitude)}`);
  return `https://www.google.com/maps/search/?api=1&query=${query}`;
}

export function safeMapsUrl(candidate, latitude, longitude) {
  try {
    const url = new URL(candidate);
    const allowedHost = url.hostname === "google.com" || url.hostname.endsWith(".google.com");
    if (url.protocol === "https:" && allowedHost && url.pathname.startsWith("/maps")) return url.href;
  } catch {
    // Fall through to a locally constructed link.
  }
  return googleMapsUrl(latitude, longitude);
}
