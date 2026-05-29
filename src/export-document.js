import fs from "node:fs/promises";
import path from "node:path";
import { resolveGeneratedJsonPath } from "./naming.js";

const DOCUMENT_DIR = path.resolve("output/document");
const EXPORT_DIR = path.resolve("output/export");

function usage() {
  console.log("Uso: npm run export-document -- <document-json>");
}

async function normalizeInputArg(arg) {
  const trimmed = String(arg || "").trim();
  if (!trimmed) return null;
  const canonicalFileName = trimmed.endsWith(".json") ? path.basename(trimmed) : `${trimmed}.json`;
  return resolveGeneratedJsonPath(DOCUMENT_DIR, trimmed, canonicalFileName);
}

function renderVerseNumber(verse) {
  if (verse === null || verse === undefined) return "?";
  return String(verse);
}

function renderBookTitle(book) {
  if (typeof book === "string" && book.trim() !== "") return book;
  return "Libro sin identificar";
}

function renderChapterTitle(chapter) {
  if (chapter === null || chapter === undefined) return "Capítulo sin identificar";
  return `Capítulo ${chapter}`;
}

function renderTxtChapterTitle(chapter) {
  if (chapter === null || chapter === undefined) return "CAPÍTULO SIN IDENTIFICAR";
  return `CAPÍTULO ${chapter}`;
}

function requiresVerseReview(verse) {
  return verse?.requires_manual_review === true || verse?.requires_review === true;
}

function renderVerseLine(verse) {
  const marker = requiresVerseReview(verse) ? " [REQUIERE REVISIÓN]" : "";
  return `${renderVerseNumber(verse?.verse)}. ${verse?.text ?? ""}${marker}`;
}

function collectSourcePages(metadata) {
  const pages = new Set();
  const generatedFrom = Array.isArray(metadata?.generated_from) ? metadata.generated_from : [];
  for (const item of generatedFrom) {
    if (typeof item !== "string" || item.trim() === "") continue;
    const base = path.basename(item);
    pages.add(base.endsWith(".json") ? base.replace(/\.json$/i, ".jpg") : base);
  }
  return [...pages];
}

function renderMarkdown(document) {
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

function renderTxt(document) {
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
  const warnings = Array.isArray(metadata.warnings) ? metadata.warnings : [];
  if (metadata.requires_manual_review === true || warnings.length) {
    const lines = ["## Revisión pendiente"];
    if (warnings.length) {
      warnings.forEach((warning) => lines.push(`- ${warning}`));
    } else {
      lines.push("- El documento requiere revisión manual.");
    }
    blocks.push(lines.join("\n"));
  }

  const trace = buildTraceLines(metadata);
  if (trace.length) {
    blocks.push(["## Trazabilidad", ...trace.map((line) => `- ${line}`)].join("\n"));
  }
}

function appendTxtMetadata(blocks, metadata) {
  const warnings = Array.isArray(metadata.warnings) ? metadata.warnings : [];
  if (metadata.requires_manual_review === true || warnings.length) {
    const lines = ["REVISIÓN PENDIENTE"];
    if (warnings.length) {
      warnings.forEach((warning) => lines.push(`- ${warning}`));
    } else {
      lines.push("- El documento requiere revisión manual.");
    }
    blocks.push(lines.join("\n"));
  }

  const trace = buildTraceLines(metadata);
  if (trace.length) {
    blocks.push(["TRAZABILIDAD", ...trace.map((line) => `- ${line}`)].join("\n"));
  }
}

function buildTraceLines(metadata) {
  const lines = [];
  const pages = collectSourcePages(metadata);
  const continuityFiles = Array.isArray(metadata.continuity_files) ? metadata.continuity_files : [];

  if (pages.length) lines.push(`Páginas fuente: ${pages.join(", ")}`);
  if (continuityFiles.length) lines.push(`Continuidad usada: ${continuityFiles.map((file) => path.basename(file)).join(", ")}`);
  lines.push(`Fecha de generación: ${new Date().toISOString()}`);

  return lines;
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length !== 1) {
    usage();
    process.exit(1);
  }

  const inputPath = await normalizeInputArg(args[0]);
  const raw = await fs.readFile(inputPath, "utf8");
  const document = JSON.parse(raw);
  const baseName = path.basename(inputPath, ".json");

  await fs.mkdir(EXPORT_DIR, { recursive: true });
  const markdownPath = path.join(EXPORT_DIR, `${baseName}.md`);
  const txtPath = path.join(EXPORT_DIR, `${baseName}.txt`);

  await fs.writeFile(markdownPath, renderMarkdown(document), "utf8");
  await fs.writeFile(txtPath, renderTxt(document), "utf8");

  console.log(`Markdown generado: ${markdownPath}`);
  console.log(`TXT generado: ${txtPath}`);
}

main().catch((error) => {
  console.error("Error exportando documento:", error.message);
  process.exit(1);
});
