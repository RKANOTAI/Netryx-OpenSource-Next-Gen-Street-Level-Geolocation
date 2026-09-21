const MAX_URL_LENGTH = 2048;
export const MAX_PHOTOS = 4;
export const MAX_PHOTO_BYTES = 10 * 1024 * 1024;

function isPrivateHost(hostname) {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  if (host === "localhost" || host.endsWith(".localhost")) return true;
  if (/^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host)) return true;
  const match = host.match(/^172\.(\d+)\./);
  if (match && Number(match[1]) >= 16 && Number(match[1]) <= 31) return true;
  if (host === "::1" || host === "0.0.0.0" || host.startsWith("fe80:")) return true;
  return false;
}

export function validateListingUrl(input) {
  const value = String(input ?? "").trim();
  if (!value) return { valid: false, message: "Saisissez l’URL publique d’une annonce." };
  if (value.length > MAX_URL_LENGTH) return { valid: false, message: "L’URL est trop longue." };
  let url;
  try {
    url = new URL(value);
  } catch {
    return { valid: false, message: "L’URL n’est pas valide." };
  }
  if (!["https:", "http:"].includes(url.protocol)) {
    return { valid: false, message: "Seules les URL HTTP et HTTPS sont acceptées." };
  }
  if (!url.hostname || isPrivateHost(url.hostname)) {
    return { valid: false, message: "Les adresses locales ou privées sont refusées." };
  }
  if (url.username || url.password) {
    return { valid: false, message: "Les URL contenant des identifiants sont refusées." };
  }
  url.hash = "";
  return { valid: true, value: url.href.replace(/\/$/, url.pathname === "/" ? "" : "/") };
}

function numberField(value, label, minimum, maximum) {
  if (value === null || value === undefined || String(value).trim() === "") {
    return { valid: false, message: `Saisissez la ${label}.` };
  }
  const number = Number(value);
  if (!Number.isFinite(number)) return { valid: false, message: `La ${label} doit être un nombre.` };
  if (number < minimum || number > maximum) {
    return { valid: false, message: `La ${label} doit être comprise entre ${minimum} et ${maximum}.` };
  }
  return { valid: true, value: number };
}

function validPhotoFile(file, index) {
  if (!file || typeof file !== "object") return { valid: false, message: `La photo ${index + 1} est invalide.` };
  const type = String(file.type ?? "").toLowerCase();
  const name = String(file.name ?? "").toLowerCase();
  const extensionAllowed = /\.(jpe?g|png)$/.test(name);
  if (!(["image/jpeg", "image/png"].includes(type) || (!type && extensionAllowed))) {
    return { valid: false, message: `La photo ${index + 1} doit être au format JPEG ou PNG.` };
  }
  if (!Number.isFinite(Number(file.size)) || Number(file.size) > MAX_PHOTO_BYTES) {
    return { valid: false, message: `La photo ${index + 1} dépasse la limite de 10 Mo.` };
  }
  return { valid: true };
}

export function validatePhotoSubmission(input = {}) {
  const photos = Array.isArray(input.photos) ? input.photos : [];
  if (photos.length === 0) return { valid: false, message: "Ajoutez au moins une photo extérieure." };
  if (photos.length > MAX_PHOTOS) return { valid: false, message: "Sélectionnez au maximum 4 photos." };

  for (let index = 0; index < photos.length; index += 1) {
    const fileResult = validPhotoFile(photos[index], index);
    if (!fileResult.valid) return fileResult;
  }

  const latitude = numberField(input.latitude, "latitude", -85, 85);
  if (!latitude.valid) return latitude;
  const longitude = numberField(input.longitude, "longitude", -180, 180);
  if (!longitude.valid) return longitude;
  const radius = numberField(input.radiusM, "rayon", 50, 1000);
  if (!radius.valid) return radius;
  if (input.reviewedExterior !== true) {
    return { valid: false, message: "Confirmez avoir vérifié que toutes les photos sont des extérieurs." };
  }
  const imageSize = input.imageSize ?? "hd";
  if (!["sd", "hd"].includes(imageSize)) return { valid: false, message: "Choisissez une qualité d’image valide." };

  return {
    valid: true,
    value: {
      photos,
      latitude: latitude.value,
      longitude: longitude.value,
      radiusM: radius.value,
      reviewedExterior: true,
      imageSize,
    },
  };
}

export function validateApiOrigin(input) {
  const value = String(input ?? "").trim();
  if (!value) return { valid: true, value: "" };
  let url;
  try {
    url = new URL(value);
  } catch {
    return { valid: false, message: "L’origine API n’est pas une URL valide." };
  }
  const local = isPrivateHost(url.hostname);
  if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    return { valid: false, message: "Saisissez uniquement l’origine de l’API, sans chemin ni identifiants." };
  }
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    return { valid: false, message: "L’API publique doit utiliser HTTPS (HTTP est réservé au local)." };
  }
  return { valid: true, value: url.origin };
}
