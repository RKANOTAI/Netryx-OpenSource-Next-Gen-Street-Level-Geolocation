export const initialState = Object.freeze({
  phase: "idle",
  mode: "photos",
  listingUrl: "",
  photos: [],
  region: { latitude: "", longitude: "", radiusM: "" },
  reviewedExterior: false,
  imageSize: "hd",
  jobId: null,
  runId: 0,
  progress: null,
  result: null,
  error: null,
});

function sameRun(state, action) {
  return action.runId == null || state.runId === action.runId;
}

export function reduce(state, action) {
  switch (action.type) {
    case "SET_MODE":
      return { ...state, mode: action.mode === "url" ? "url" : "photos" };
    case "PHOTOS_SET":
      return {
        ...state,
        photos: Array.isArray(action.photos) ? action.photos : [],
        photoSelectionError: action.error ?? null,
        reviewedExterior: false,
      };
    case "PHOTO_REMOVE":
      return {
        ...state,
        photos: state.photos.filter((photo) => photo.id !== action.id),
        photoSelectionError: null,
        reviewedExterior: false,
      };
    case "SET_REGION":
      return { ...state, region: { ...state.region, ...action.region } };
    case "SET_REVIEWED_EXTERIOR":
      return { ...state, reviewedExterior: Boolean(action.value) };
    case "SET_IMAGE_SIZE":
      return { ...state, imageSize: action.value === "sd" ? "sd" : "hd" };
    case "SUBMIT_START":
      return {
        ...state,
        phase: "submitting",
        listingUrl: action.listingUrl ?? state.listingUrl,
        runId: action.runId ?? state.runId + 1,
        jobId: null,
        result: null,
        error: null,
        progress: { phase: "queued", percent: null, message_fr: "Envoi de la recherche…" },
      };
    case "SUBMIT_INVALID":
      return { ...state, phase: "invalid", error: { message_fr: action.message, field: action.field ?? state.mode } };
    case "JOB_ACCEPTED":
      if (!sameRun(state, action)) return state;
      return {
        ...state,
        phase: "queued",
        jobId: action.accepted.job_id,
        progress: { phase: "queued", percent: 0, message_fr: "Analyse placée dans la file d’attente." },
      };
    case "JOB_UPDATED": {
      if (!sameRun(state, action)) return state;
      const snapshot = action.snapshot;
      if (state.jobId && snapshot.job_id !== state.jobId) return state;
      return {
        ...state,
        phase: snapshot.status,
        progress: snapshot.progress ?? null,
        result: snapshot.result ?? null,
        error: snapshot.error ?? null,
      };
    }
    case "REQUEST_FAILED":
      if (!sameRun(state, action)) return state;
      return {
        ...state,
        phase: "failed",
        error: { message_fr: action.message, retryable: true },
      };
    case "RESET":
      return {
        ...initialState,
        mode: state.mode,
        listingUrl: state.listingUrl,
        photos: state.photos,
        region: state.region,
        reviewedExterior: state.reviewedExterior,
        imageSize: state.imageSize,
        runId: state.runId + 1,
      };
    default:
      return state;
  }
}
