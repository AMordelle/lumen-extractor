import fs from "node:fs/promises";
import path from "node:path";

export function imageStem(imageName) {
  const base = path.basename(String(imageName || "").trim());
  return path.parse(base).name;
}

export function imageNameFromStem(stem) {
  return `${imageStem(stem)}.jpg`;
}

export function pageJsonName(imageName) {
  return `${imageStem(imageName)}.json`;
}

export function reviewJsonName(imageName) {
  return `${imageStem(imageName)}.review.json`;
}

export function auditJsonName(imageName) {
  return `${imageStem(imageName)}.audit.json`;
}

export function rawResponseJsonName(imageName) {
  return `${imageStem(imageName)}.raw.json`;
}

export function canonicalRangeStem(images) {
  if (!Array.isArray(images) || images.length === 0) {
    throw new Error("No se puede construir un rango canónico sin imágenes.");
  }
  const first = imageStem(images[0]);
  const last = imageStem(images[images.length - 1]);
  return `${first}__${last}`;
}

export function rangeJsonName(images) {
  return `${canonicalRangeStem(images)}.json`;
}

function stripKnownSuffixes(stem) {
  return stem
    .replace(/\.review$/i, "")
    .replace(/\.audit$/i, "")
    .replace(/\.raw$/i, "")
    .replace(/_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:-\d{3})?Z$/i, "");
}

export function imageNameFromGeneratedJsonPath(jsonPath) {
  const stem = stripKnownSuffixes(path.basename(jsonPath, ".json"));
  return imageNameFromStem(stem);
}

function legacyPrefixForJsonName(fileName) {
  const stem = stripKnownSuffixes(path.basename(fileName, ".json"));
  return `${stem}_`;
}

export async function resolveGeneratedJsonPath(dir, input, canonicalFileName) {
  const trimmed = String(input || "").trim();
  if (!trimmed) return null;

  const looksLikePath = trimmed.endsWith(".json") || trimmed.includes("/") || trimmed.includes("\\");
  const requestedPath = looksLikePath
    ? (path.isAbsolute(trimmed) ? trimmed : path.resolve(trimmed))
    : path.join(dir, canonicalFileName ?? `${imageStem(trimmed)}.json`);

  try {
    await fs.access(requestedPath);
    return requestedPath;
  } catch {
    // Continúa con compatibilidad razonable para nombres generados antes de PR13.
  }

  const requestedDir = path.dirname(requestedPath);
  const requestedFile = path.basename(requestedPath);
  const canonicalStem = stripKnownSuffixes(path.basename(requestedFile, ".json"));
  const legacyPrefix = legacyPrefixForJsonName(requestedFile);

  const entries = await fs.readdir(requestedDir, { withFileTypes: true }).catch(() => []);
  const candidates = entries
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name)
    .filter((name) => name.endsWith(".json"))
    .filter((name) => name === requestedFile || name.startsWith(legacyPrefix) || path.basename(name, ".json") === canonicalStem)
    .sort();

  if (candidates.length === 0) return requestedPath;
  return path.join(requestedDir, candidates[candidates.length - 1]);
}
