import fs from "node:fs/promises";
import path from "node:path";

const PAGES_JSON_DIR = path.resolve("output/pages_json");
const CONTINUITY_DIR = path.resolve("output/continuity");
const DOCUMENT_DIR = path.resolve("output/document");

function usage() {
  console.log("Uso: npm run build-document -- <inputs>");
  console.log("Ejemplos:");
  console.log("  npm run build-document -- AI156_0018 AI156_0019 AI156_0020");
  console.log("  npm run build-document -- output/pages_json/AI156_0018.json output/pages_json/AI156_0019.json");
  console.log("  npm run build-document -- output/continuity/AI156_0018__AI156_0020.json");
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

function isContinuityFile(inputPath) {
  return inputPath.includes(`${path.sep}output${path.sep}continuity${path.sep}`);
}

function buildOutputFileName(images) {
  const first = images[0].replace(/\.jpg$/i, "");
  const last = images[images.length - 1].replace(/\.jpg$/i, "");
  return `${first}__${last}.json`;
}

function pushWarning(metadata, message) {
  metadata.warnings.push(message);
  metadata.requires_manual_review = true;
}

async function loadContinuityConnections(paths, metadata) {
  const byVerse = new Map();
  const usedFiles = [];

  for (const filePath of paths) {
    const raw = await fs.readFile(filePath, "utf8");
    const parsed = JSON.parse(raw);
    const connections = Array.isArray(parsed.connections) ? parsed.connections : [];
    usedFiles.push(filePath);

    for (const c of connections) {
      if (!c || typeof c !== "object") continue;
      if (typeof c.resolved_text !== "string" || c.resolved_text.trim() === "") continue;
      const key = verseKey(c.book ?? null, c.chapter ?? null, c.verse ?? null);
      const existing = byVerse.get(key);
      if (existing && existing.resolved_text !== c.resolved_text) {
        pushWarning(metadata, `Continuidad contradictoria para ${key} entre archivos de continuidad.`);
        continue;
      }
      byVerse.set(key, { ...c, key });
    }
  }

  return { byVerse, usedFiles };
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

function materializeDocument(flatVerses, continuityMap, metadata) {
  const books = [];
  const booksMap = new Map();
  const seenComplete = new Map();
  const consumedByContinuity = new Set();

  flatVerses.forEach((entry) => {
    const key = verseKey(entry.book, entry.chapter, entry.verse);
    const continuity = continuityMap.get(key);
    const sourceRef = { image: entry.image, position: entry.position || "complete_on_page" };

    if (continuity) {
      const uniqueConnection = `${continuity.from_image}->${continuity.to_image}|${key}`;
      if (consumedByContinuity.has(uniqueConnection)) return;
      consumedByContinuity.add(uniqueConnection);

      if (continuity.requires_manual_review) {
        pushWarning(metadata, `Continuidad con revisión manual requerida para ${key}.`);
      }

      upsertVerse(books, booksMap, entry, continuity.resolved_text, [sourceRef], true);
      return;
    }

    const completeKey = `${key}|${entry.text}`;
    if (entry.verse !== null && !entry.is_partial && seenComplete.has(completeKey)) {
      pushWarning(metadata, `Versículo duplicado detectado: ${key} en ${entry.image}.`);
      return;
    }
    seenComplete.set(completeKey, true);

    if (entry.is_partial || entry.position === "continues_on_next_page" || entry.position === "continues_from_previous_page") {
      pushWarning(metadata, `Versículo parcial sin continuidad resuelta: ${key} en ${entry.image}.`);
    }

    upsertVerse(books, booksMap, entry, entry.text, [sourceRef], false);
  });

  return books;
}

function upsertVerse(books, booksMap, entry, text, sources) {
  const bookName = entry.book ?? "__UNKNOWN_BOOK__";
  if (!booksMap.has(bookName)) {
    const bookNode = { book: entry.book, chapters: [] };
    books.push(bookNode);
    booksMap.set(bookName, { node: bookNode, chapterMap: new Map() });
  }

  const bookState = booksMap.get(bookName);
  const chapterKey = entry.chapter ?? "__UNKNOWN_CHAPTER__";
  if (!bookState.chapterMap.has(chapterKey)) {
    const chapterNode = { chapter: entry.chapter, verses: [] };
    bookState.node.chapters.push(chapterNode);
    bookState.chapterMap.set(chapterKey, chapterNode);
  }

  const chapterNode = bookState.chapterMap.get(chapterKey);
  chapterNode.verses.push({ verse: entry.verse, text, sources });
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length === 0) {
    usage();
    process.exit(1);
  }

  const metadata = {
    generated_from: [],
    continuity_files: [],
    warnings: [],
    requires_manual_review: false,
  };

  const normalizedInputs = args.map(normalizeInputArg).filter(Boolean);
  const continuityPaths = normalizedInputs.filter(isContinuityFile);
  let pagePaths = normalizedInputs.filter((p) => !isContinuityFile(p));

  const { byVerse, usedFiles } = await loadContinuityConnections(continuityPaths, metadata);
  metadata.continuity_files = usedFiles;

  if (pagePaths.length === 0) {
    const images = [];
    for (const c of byVerse.values()) {
      if (typeof c.from_image === "string") images.push(c.from_image);
      if (typeof c.to_image === "string") images.push(c.to_image);
    }
    const uniqueImageBases = [...new Set(images)].map((img) => img.replace(/\.jpg$/i, ""));
    pagePaths = uniqueImageBases.map((base) => path.join(PAGES_JSON_DIR, `${base}.json`));
  }

  const pages = [];
  for (const pagePath of pagePaths) {
    const raw = await fs.readFile(pagePath, "utf8");
    const parsed = JSON.parse(raw);
    pages.push({ path: pagePath, payload: parsed });
  }

  metadata.generated_from = pages.map((p) => p.path);

  const flatVerses = pages.flatMap((page, idx) => collectPageVerses(page.path, page.payload, idx));
  const books = materializeDocument(flatVerses, byVerse, metadata);

  const documentOutput = { books, metadata };

  await fs.mkdir(DOCUMENT_DIR, { recursive: true });
  const images = pages.map((p) => p.payload.image || getImageNameFromPath(p.path));
  const outputPath = path.join(DOCUMENT_DIR, buildOutputFileName(images));
  await fs.writeFile(outputPath, `${JSON.stringify(documentOutput, null, 2)}\n`, "utf8");

  console.log(`Documento generado: ${outputPath}`);
  console.log(`Libros: ${books.length}`);
  console.log(`Warnings: ${metadata.warnings.length}`);
}

main().catch((error) => {
  console.error("Error construyendo documento:", error.message);
  process.exit(1);
});
