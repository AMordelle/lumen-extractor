import fs from "node:fs/promises";
import path from "node:path";
import { imageNameFromGeneratedJsonPath, pageJsonName, rangeJsonName, resolveGeneratedJsonPath } from "./naming.js";

const PAGES_JSON_DIR = path.resolve("output/pages_json");
const CONTINUITY_DIR = path.resolve("output/continuity");

function usage() {
  console.log("Uso: npm run continuity -- AI156_0018 AI156_0019");
  console.log("   o: npm run continuity -- output/pages_json/AI156_0018.json output/pages_json/AI156_0019.json");
}

async function normalizeInputArg(arg) {
  const trimmed = String(arg || "").trim();
  if (!trimmed) return null;
  return resolveGeneratedJsonPath(PAGES_JSON_DIR, trimmed, pageJsonName(trimmed));
}

function getImageNameFromPath(jsonPath) {
  return imageNameFromGeneratedJsonPath(jsonPath);
}

function flattenVerses(page) {
  const out = [];
  (page.sections || []).forEach((section) => {
    (section.verses || []).forEach((verse) => {
      if (!verse || typeof verse.text !== "string" || verse.text.trim() === "") return;
      out.push({
        book: section.book ?? null,
        chapter: section.chapter ?? null,
        verse: verse.verse ?? null,
        text: verse.text,
        position: verse.position,
        is_partial: verse.is_partial === true,
      });
    });
  });
  return out;
}

function getLastCandidate(verses) {
  for (let i = verses.length - 1; i >= 0; i -= 1) {
    const v = verses[i];
    if (v.position === "continues_on_next_page" || v.is_partial === true) return v;
  }
  return null;
}

function getFirstCandidate(verses) {
  for (let i = 0; i < verses.length; i += 1) {
    const v = verses[i];
    if (v.position === "continues_from_previous_page" || v.position === "fragment_without_visible_number") return v;
    if (v.verse !== null) break;
  }
  return null;
}

function isEvidentCut(text) {
  if (typeof text !== "string") return false;
  return /[-–—]\s*$/.test(text.trimEnd());
}

function normalizeBookName(book) {
  if (typeof book !== "string") return null;
  return book
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim()
    .replace(/\s+/g, " ");
}

function buildResolvedText(previousFragment, nextFragment) {
  const prev = previousFragment.trimEnd();
  const next = nextFragment.trimStart();

  if (/-\s*$/.test(prev)) {
    const prevWithoutHyphen = prev.replace(/-\s*$/, "");
    const m = next.match(/^(\S+)([\s\S]*)$/);
    if (!m) return prevWithoutHyphen;
    const [, firstWord, rest] = m;
    return `${prevWithoutHyphen}${firstWord}${rest}`;
  }

  if (!prev) return next;
  if (!next) return prev;
  return `${prev} ${next}`;
}

function assessConnection(prev, next) {
  const notes = [];
  let strongSignals = 0;

  const prevBookNormalized = normalizeBookName(prev.book);
  const nextBookNormalized = normalizeBookName(next.book);

  if (prevBookNormalized && nextBookNormalized && prevBookNormalized === nextBookNormalized) {
    strongSignals += 1;
  } else if (prevBookNormalized && nextBookNormalized && prevBookNormalized !== nextBookNormalized) {
    return null;
  } else {
    notes.push("Book ausente o no normalizable en uno de los fragmentos.");
  }

  if (prev.chapter !== null && next.chapter !== null && prev.chapter === next.chapter) {
    strongSignals += 1;
  } else if (prev.chapter !== null && next.chapter !== null && prev.chapter !== next.chapter) {
    return null;
  } else {
    notes.push("Chapter ausente en uno de los fragmentos.");
  }

  const previousHasCut = isEvidentCut(prev.text) || prev.position === "continues_on_next_page" || prev.is_partial;
  if (previousHasCut) {
    strongSignals += 1;
  } else {
    notes.push("No hay señal fuerte de corte al final del fragmento previo.");
  }

  const nextStartsAsFragment = next.position === "continues_from_previous_page" || next.position === "fragment_without_visible_number";
  if (nextStartsAsFragment) {
    strongSignals += 1;
  } else {
    notes.push("El siguiente fragmento no está marcado como continuidad de inicio.");
  }

  const resolvedText = buildResolvedText(prev.text, next.text);

  if (strongSignals >= 4) {
    return { confidence: "high", requires_manual_review: false, notes: [], resolvedText };
  }

  if (strongSignals >= 2) {
    return {
      confidence: "low",
      requires_manual_review: true,
      notes: notes.length ? notes : ["Señales parciales de continuidad; requiere verificación manual."],
      resolvedText,
    };
  }

  return null;
}

function buildOutputFileName(images) {
  return rangeJsonName(images);
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length < 2) {
    usage();
    process.exit(1);
  }

  const files = (await Promise.all(args.map(normalizeInputArg))).filter(Boolean);
  const pages = [];

  for (const filePath of files) {
    const raw = await fs.readFile(filePath, "utf8");
    const parsed = JSON.parse(raw);
    pages.push({
      path: filePath,
      image: parsed.image || getImageNameFromPath(filePath),
      payload: parsed,
      verses: flattenVerses(parsed),
    });
  }

  const images = pages.map((page) => page.image);
  const connections = [];

  for (let i = 0; i < pages.length - 1; i += 1) {
    const current = pages[i];
    const next = pages[i + 1];
    const previousFragment = getLastCandidate(current.verses);
    const nextFragment = getFirstCandidate(next.verses);

    if (!previousFragment || !nextFragment) continue;

    const assessed = assessConnection(previousFragment, nextFragment);
    if (!assessed) continue;

    const inheritedReference = nextFragment.verse === null
      ? {
          book: previousFragment.book ?? nextFragment.book ?? null,
          chapter: previousFragment.chapter ?? nextFragment.chapter ?? null,
          verse: previousFragment.verse ?? nextFragment.verse ?? null,
        }
      : {
          book: previousFragment.book ?? nextFragment.book ?? null,
          chapter: previousFragment.chapter ?? nextFragment.chapter ?? null,
          verse: previousFragment.verse ?? nextFragment.verse ?? null,
        };

    connections.push({
      from_image: current.image,
      to_image: next.image,
      book: inheritedReference.book,
      chapter: inheritedReference.chapter,
      verse: inheritedReference.verse,
      previous_fragment: previousFragment.text,
      next_fragment: nextFragment.text,
      resolved_text: assessed.resolvedText,
      confidence: assessed.confidence,
      requires_manual_review: assessed.requires_manual_review,
      notes: assessed.notes,
    });
  }

  await fs.mkdir(CONTINUITY_DIR, { recursive: true });
  const output = { images, connections };
  const outputFile = path.join(CONTINUITY_DIR, buildOutputFileName(images));
  await fs.writeFile(outputFile, `${JSON.stringify(output, null, 2)}\n`, "utf8");

  console.log(`Continuidad generada: ${outputFile}`);
  console.log(`Conexiones detectadas: ${connections.length}`);
}

main().catch((error) => {
  console.error("Error construyendo continuidad:", error.message);
  process.exit(1);
});
