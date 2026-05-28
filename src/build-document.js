import fs from "node:fs/promises";
import path from "node:path";

const PAGES_JSON_DIR = path.resolve("output/pages_json");
const CONTINUITY_DIR = path.resolve("output/continuity");
const DOCUMENT_DIR = path.resolve("output/document");

function usage() {
  console.log("Uso: npm run build-document -- <inputs>");
}

function normalizeInputArg(arg) {
  const trimmed = String(arg || "").trim();
  if (!trimmed) return null;
  if (trimmed.endsWith(".json") || trimmed.includes("/") || trimmed.includes("\\")) {
    return path.isAbsolute(trimmed) ? trimmed : path.resolve(trimmed);
  }
  const baseName = trimmed.endsWith(".jpg") ? trimmed.slice(0, -4) : trimmed;
  return path.join(PAGES_JSON_DIR, `${baseName}.json`);
}

function getImageNameFromPath(jsonPath) {
  return `${path.basename(jsonPath, ".json")}.jpg`;
}

function normalizeBookName(book) {
  if (typeof book !== "string") return null;
  return book.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim().replace(/\s+/g, " ");
}

function verseKey(book, chapter, verse) {
  return `${normalizeBookName(book) || "null"}|${chapter ?? "null"}|${verse ?? "null"}`;
}

function connectionKey(c) {
  return `${c.from_image}|${c.to_image}|${verseKey(c.book ?? null, c.chapter ?? null, c.verse ?? null)}`;
}

function isContinuityFile(inputPath) {
  return inputPath.includes(`${path.sep}output${path.sep}continuity${path.sep}`);
}

function buildOutputFileName(images) {
  const first = images[0].replace(/\.jpg$/i, "");
  const last = images[images.length - 1].replace(/\.jpg$/i, "");
  return `${first}__${last}.json`;
}

function addWarning(metadata, message, manual = true) {
  metadata.warnings.push(message);
  if (manual) metadata.requires_manual_review = true;
}

async function discoverContinuityFiles(pageImages) {
  const entries = await fs.readdir(CONTINUITY_DIR, { withFileTypes: true }).catch(() => []);
  const pageSet = new Set(pageImages);
  const files = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    const filePath = path.join(CONTINUITY_DIR, entry.name);
    try {
      const raw = await fs.readFile(filePath, "utf8");
      const parsed = JSON.parse(raw);
      const images = Array.isArray(parsed.images) ? parsed.images : [];
      const isRelevant = images.some((img) => pageSet.has(img));
      if (isRelevant) files.push(filePath);
    } catch {
      // archivo inválido: ignorar en descubrimiento automático
    }
  }
  return files.sort();
}

async function loadContinuityConnections(paths, metadata, allowedImages = null) {
  const byConnection = new Map();
  const usedFiles = new Set();

  for (const filePath of paths) {
    const raw = await fs.readFile(filePath, "utf8");
    const parsed = JSON.parse(raw);
    const connections = Array.isArray(parsed.connections) ? parsed.connections : [];
    let fileUsed = false;

    for (const c of connections) {
      if (!c || typeof c !== "object") continue;
      if (typeof c.resolved_text !== "string" || c.resolved_text.trim() === "") continue;
      if (allowedImages) {
        if (!allowedImages.has(c.from_image) || !allowedImages.has(c.to_image)) continue;
      }
      const key = connectionKey(c);
      const existing = byConnection.get(key);
      if (existing && existing.resolved_text !== c.resolved_text) {
        addWarning(metadata, `Continuidad contradictoria para ${key}.`);
        continue;
      }
      byConnection.set(key, c);
      fileUsed = true;
    }

    if (fileUsed) usedFiles.add(filePath);
  }

  return { byConnection, usedFiles: [...usedFiles].sort() };
}

function collectPageVerses(pagePath, parsed, pageIndex) {
  const image = parsed.image || getImageNameFromPath(pagePath);
  const verses = [];
  (parsed.sections || []).forEach((section, sectionIndex) => {
    (section.verses || []).forEach((v, verseIndex) => {
      if (!v || typeof v.text !== "string" || v.text.trim() === "") return;
      verses.push({
        image,
        pagePath,
        pageIndex,
        sectionIndex,
        verseIndex,
        book: section.book ?? null,
        chapter: section.chapter ?? null,
        verse: v.verse ?? null,
        text: v.text,
        position: v.position ?? null,
        is_partial: v.is_partial === true,
      });
    });
  });
  return verses;
}

function findContinuityForEntry(entry, byConnection) {
  if (entry.verse === null) return null;
  for (const c of byConnection.values()) {
    if (c.from_image !== entry.image) continue;
    if (verseKey(c.book ?? null, c.chapter ?? null, c.verse ?? null) !== verseKey(entry.book, entry.chapter, entry.verse)) continue;
    return c;
  }
  return null;
}

function upsertVerse(books, booksMap, entry, text, sources) {
  const bookKey = entry.book ?? "__UNKNOWN_BOOK__";
  if (!booksMap.has(bookKey)) {
    const node = { book: entry.book, chapters: [] };
    books.push(node);
    booksMap.set(bookKey, { node, chapters: new Map() });
  }
  const state = booksMap.get(bookKey);
  const chapterKey = entry.chapter ?? "__UNKNOWN_CHAPTER__";
  if (!state.chapters.has(chapterKey)) {
    const chapter = { chapter: entry.chapter, verses: [] };
    state.node.chapters.push(chapter);
    state.chapters.set(chapterKey, chapter);
  }
  state.chapters.get(chapterKey).verses.push({ verse: entry.verse, text, sources });
}

function materializeDocument(flatVerses, byConnection, metadata) {
  const books = [];
  const booksMap = new Map();
  const consumedConnection = new Set();
  const absorbedTargetFragments = new Set();

  for (const entry of flatVerses) {
    const localId = `${entry.image}|${entry.sectionIndex}|${entry.verseIndex}`;
    if (absorbedTargetFragments.has(localId)) continue;

    const c = findContinuityForEntry(entry, byConnection);
    if (c && !consumedConnection.has(connectionKey(c))) {
      const connId = connectionKey(c);
      consumedConnection.add(connId);

      // absorber fragmento de inicio en to_image
      flatVerses.forEach((v) => {
        const isTarget = v.image === c.to_image;
        const isSameRef = verseKey(v.book, v.chapter, v.verse) === verseKey(c.book ?? null, c.chapter ?? null, c.verse ?? null);
        const isInitialFragment = v.verse === null && (v.position === "continues_from_previous_page" || v.position === "fragment_without_visible_number");
        if (isTarget && (isInitialFragment || isSameRef)) {
          absorbedTargetFragments.add(`${v.image}|${v.sectionIndex}|${v.verseIndex}`);
        }
      });

      const sources = [
        { image: c.from_image, position: "continues_on_next_page" },
        { image: c.to_image, position: "continues_from_previous_page" },
      ];
      upsertVerse(books, booksMap, { ...entry, book: c.book ?? entry.book, chapter: c.chapter ?? entry.chapter, verse: c.verse ?? entry.verse }, c.resolved_text, sources);

      if (c.requires_manual_review === true || c.confidence === "low") {
        addWarning(metadata, `Continuidad aplicada con revisión pendiente: ${connId}.`);
      }
      continue;
    }

    const isUnresolvedPartial = entry.is_partial || entry.position === "continues_on_next_page" || (entry.verse === null && (entry.position === "continues_from_previous_page" || entry.position === "fragment_without_visible_number"));
    if (isUnresolvedPartial) {
      addWarning(metadata, `Continuidad no resuelta para fragmento en ${entry.image} (${entry.book ?? "null"}/${entry.chapter ?? "null"}/${entry.verse ?? "null"}).`);
    }

    upsertVerse(books, booksMap, entry, entry.text, [{ image: entry.image, position: entry.position || "complete_on_page" }]);
  }

  return books;
}

async function main() {
  const args = process.argv.slice(2);
  if (!args.length) {
    usage();
    process.exit(1);
  }

  const metadata = { generated_from: [], continuity_files: [], warnings: [], requires_manual_review: false };
  const normalizedInputs = args.map(normalizeInputArg).filter(Boolean);
  const explicitContinuityPaths = normalizedInputs.filter(isContinuityFile);
  let pagePaths = normalizedInputs.filter((p) => !isContinuityFile(p));

  if (!pagePaths.length && explicitContinuityPaths.length) {
    const { byConnection } = await loadContinuityConnections(explicitContinuityPaths, metadata);
    const images = new Set();
    for (const c of byConnection.values()) {
      images.add(c.from_image);
      images.add(c.to_image);
    }
    pagePaths = [...images].map((img) => path.join(PAGES_JSON_DIR, img.replace(/\.jpg$/i, "") + ".json"));
  }

  const pages = [];
  for (const pagePath of pagePaths) {
    const raw = await fs.readFile(pagePath, "utf8");
    pages.push({ path: pagePath, payload: JSON.parse(raw) });
  }

  metadata.generated_from = pages.map((p) => p.path);
  const pageImages = pages.map((p) => p.payload.image || getImageNameFromPath(p.path));
  const autoContinuityPaths = await discoverContinuityFiles(pageImages);
  const continuityPaths = [...new Set([...explicitContinuityPaths, ...autoContinuityPaths])];

  const { byConnection, usedFiles } = await loadContinuityConnections(continuityPaths, metadata, new Set(pageImages));
  metadata.continuity_files = usedFiles;

  const flatVerses = pages.flatMap((page, idx) => collectPageVerses(page.path, page.payload, idx));
  const books = materializeDocument(flatVerses, byConnection, metadata);

  await fs.mkdir(DOCUMENT_DIR, { recursive: true });
  const outputPath = path.join(DOCUMENT_DIR, buildOutputFileName(pageImages));
  await fs.writeFile(outputPath, `${JSON.stringify({ books, metadata }, null, 2)}\n`, "utf8");

  console.log(`Documento generado: ${outputPath}`);
  console.log(`Archivos de continuidad usados: ${metadata.continuity_files.length}`);
  console.log(`Warnings: ${metadata.warnings.length}`);
}

main().catch((error) => {
  console.error("Error construyendo documento:", error.message);
  process.exit(1);
});
