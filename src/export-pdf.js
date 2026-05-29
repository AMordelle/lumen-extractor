import fs from "node:fs/promises";
import path from "node:path";
import {
  buildReviewLines,
  buildTraceLines,
  EXPORT_DIR,
  loadDocument,
  normalizeDocumentInputArg,
  renderBookTitle,
  renderChapterTitle,
  renderVerseNumber,
  requiresVerseReview,
} from "./document-export-helpers.js";
import { PdfWriter, pdfLayout } from "./pdf-writer.js";

function usage() {
  console.log("Uso: npm run export-pdf -- <document-json-or-basename>");
}

function renderPdf(document) {
  const pdf = new PdfWriter();
  const books = Array.isArray(document.books) ? document.books : [];

  books.forEach((book, bookIndex) => {
    if (bookIndex > 0) pdf.addPage();
    pdf.drawWrapped(renderBookTitle(book?.book), {
      font: "F2",
      size: pdfLayout.bookSize,
      leading: pdfLayout.bookLeading,
      spacingAfter: 10,
    });

    const chapters = Array.isArray(book?.chapters) ? book.chapters : [];
    for (const chapter of chapters) {
      pdf.moveDown(7);
      pdf.drawWrapped(renderChapterTitle(chapter?.chapter), {
        font: "F2",
        size: pdfLayout.chapterSize,
        leading: pdfLayout.chapterLeading,
        spacingAfter: 4,
      });

      const verses = Array.isArray(chapter?.verses) ? chapter.verses : [];
      for (const verse of verses) {
        const marker = requiresVerseReview(verse) ? " [REQUIERE REVISIÓN]" : "";
        pdf.drawWrapped(`${verse?.text ?? ""}${marker}`, {
          firstPrefix: `${renderVerseNumber(verse?.verse)}. `,
          nextPrefix: "   ",
          indent: 0,
          size: pdfLayout.bodySize,
          leading: pdfLayout.bodyLeading,
          spacingAfter: 2,
        });
      }
    }
  });

  appendPdfMetadata(pdf, document.metadata || {});
  return pdf.toBuffer();
}

function appendPdfMetadata(pdf, metadata) {
  const reviewLines = buildReviewLines(metadata);
  if (reviewLines.length) {
    pdf.addPage();
    pdf.drawWrapped("Revisión pendiente", {
      font: "F2",
      size: pdfLayout.chapterSize,
      leading: pdfLayout.chapterLeading,
      spacingAfter: 8,
    });
    for (const line of reviewLines) {
      pdf.drawWrapped(line, {
        size: pdfLayout.smallSize,
        leading: pdfLayout.smallLeading,
        spacingAfter: 2,
      });
    }
  }

  const trace = buildTraceLines(metadata);
  if (trace.length) {
    if (!reviewLines.length) pdf.addPage();
    else pdf.moveDown(14);
    pdf.drawWrapped("Trazabilidad", {
      font: "F2",
      size: pdfLayout.chapterSize,
      leading: pdfLayout.chapterLeading,
      spacingAfter: 8,
    });
    for (const line of trace) {
      pdf.drawWrapped(`- ${line}`, {
        size: pdfLayout.smallSize,
        leading: pdfLayout.smallLeading,
        spacingAfter: 2,
      });
    }
  }
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length !== 1) {
    usage();
    process.exit(1);
  }

  const inputPath = await normalizeDocumentInputArg(args[0]);
  const document = await loadDocument(inputPath);
  const baseName = path.basename(inputPath, ".json");

  await fs.mkdir(EXPORT_DIR, { recursive: true });
  const pdfPath = path.join(EXPORT_DIR, `${baseName}.pdf`);
  await fs.writeFile(pdfPath, renderPdf(document));

  console.log(`PDF generado: ${pdfPath}`);
}

main().catch((error) => {
  console.error("Error exportando PDF:", error.message);
  process.exit(1);
});
