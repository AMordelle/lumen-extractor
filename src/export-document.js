import fs from "node:fs/promises";
import path from "node:path";
import {
  EXPORT_DIR,
  loadDocument,
  normalizeDocumentInputArg,
  renderMarkdown,
  renderTxt,
} from "./document-export-helpers.js";

function usage() {
  console.log("Uso: npm run export-document -- <document-json-or-basename>");
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
