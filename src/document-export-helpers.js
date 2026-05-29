import fs from "node:fs/promises";
import path from "node:path";
import { resolveGeneratedJsonPath } from "./naming.js";

export const DOCUMENT_DIR = path.resolve("output/document");
export const EXPORT_DIR = path.resolve("output/export");

export async function normalizeDocumentInputArg(arg) {
  const trimmed = String(arg || "").trim();
  if (!trimmed) return null;
  const canonicalFileName = trimmed.endsWith(".json") ? path.basename(trimmed) : `${trimmed}.json`;
  return resolveGeneratedJsonPath(DOCUMENT_DIR, trimmed, canonicalFileName);
}

export async function loadDocument(inputPath) {
  const raw = await fs.readFile(inputPath, "utf8");
  return JSON.parse(raw);
}

export function renderVerseNumber(verse) {
  if (verse === null || verse === undefined) return "?";
  return String(verse);
}

export function renderBookTitle(book) {
  if (typeof book === "string" && book.trim() !== "") return book;
  return "Libro sin identificar";
}

export function renderChapterTitle(chapter) {
  if (chapter === null || chapter === undefined) return "Capítulo sin identificar";
  return `Capítulo ${chapter}`;
}

export function renderTxtChapterTitle(chapter) {
  if (chapter === null || chapter === undefined) return "CAPÍTULO SIN IDENTIFICAR";
  return `CAPÍTULO ${chapter}`;
}

export function requiresVerseReview(verse) {
  return verse?.requires_manual_review === true || verse?.requires_review === true;
}

export function renderVerseLine(verse) {
  const marker = requiresVerseReview(verse) ? " [REQUIERE REVISIÓN]" : "";
  return `${renderVerseNumber(verse?.verse)}. ${verse?.text ?? ""}${marker}`;
}

export function collectSourcePages(metadata) {
  const pages = new Set();
  const generatedFrom = Array.isArray(metadata?.generated_from) ? metadata.generated_from : [];
  for (const item of generatedFrom) {
    if (typeof item !== "string" || item.trim() === "") continue;
    const base = path.basename(item);
    pages.add(base.endsWith(".json") ? base.replace(/\.json$/i, ".jpg") : base);
  }
  return [...pages];
}

export function buildTraceLines(metadata, now = new Date()) {
  const lines = [];
  const pages = collectSourcePages(metadata);
  const continuityFiles = Array.isArray(metadata?.continuity_files) ? metadata.continuity_files : [];

  if (pages.length) lines.push(`Páginas fuente: ${pages.join(", ")}`);
  if (continuityFiles.length) lines.push(`Continuidad usada: ${continuityFiles.map((file) => path.basename(file)).join(", ")}`);
  lines.push(`Fecha de generación: ${now.toISOString()}`);

  return lines;
}

export function buildReviewLines(metadata) {
  const warnings = Array.isArray(metadata?.warnings) ? metadata.warnings : [];
  if (metadata?.requires_manual_review !== true && warnings.length === 0) return [];
  if (warnings.length) return warnings.map((warning) => `- ${warning}`);
  return ["- El documento requiere revisión manual."];
}

export function renderMarkdown(document) {
  const blocks = [];
  const books = Array.isArray(document.books) ? document.books : [];

  for (const book of books) {
    blocks.push(`# ${renderBookTitle(book?.book)}`);

    const chapters = Array.isArray(book?.chapters) ? book.chapters : [];
    for (const chapter of chapters) {
      const lines = [`## ${renderChapterTitle(chapter?.chapter)}`];
      const verses = Array.isArray(chapter?.verses) ? chapter.verses : [];
      for (const verse of verses) {
        lines.push(renderVerseLine(verse));
      }
      blocks.push(lines.join("\n"));
    }
  }

  appendMarkdownMetadata(blocks, document.metadata || {});
  return `${blocks.join("\n\n")}\n`;
}

export function renderTxt(document) {
  const blocks = [];
  const books = Array.isArray(document.books) ? document.books : [];

  for (const book of books) {
    blocks.push(renderBookTitle(book?.book).toLocaleUpperCase("es"));

    const chapters = Array.isArray(book?.chapters) ? book.chapters : [];
    for (const chapter of chapters) {
      const lines = [renderTxtChapterTitle(chapter?.chapter)];
      const verses = Array.isArray(chapter?.verses) ? chapter.verses : [];
      for (const verse of verses) {
        lines.push(renderVerseLine(verse));
      }
      blocks.push(lines.join("\n"));
    }
  }

  appendTxtMetadata(blocks, document.metadata || {});
  return `${blocks.join("\n\n")}\n`;
}

function appendMarkdownMetadata(blocks, metadata) {
  const reviewLines = buildReviewLines(metadata);
  if (reviewLines.length) {
    blocks.push(["## Revisión pendiente", ...reviewLines].join("\n"));
  }

  const trace = buildTraceLines(metadata);
  if (trace.length) {
    blocks.push(["## Trazabilidad", ...trace.map((line) => `- ${line}`)].join("\n"));
  }
}

function appendTxtMetadata(blocks, metadata) {
  const reviewLines = buildReviewLines(metadata);
  if (reviewLines.length) {
    blocks.push(["REVISIÓN PENDIENTE", ...reviewLines].join("\n"));
  }

  const trace = buildTraceLines(metadata);
  if (trace.length) {
    blocks.push(["TRAZABILIDAD", ...trace.map((line) => `- ${line}`)].join("\n"));
  }
}
