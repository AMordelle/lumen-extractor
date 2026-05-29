import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import OpenAI from "openai";
import { imageNameFromStem, imageStem, pageJsonName, reviewJsonName, rangeSummaryJsonName } from "./naming.js";

const IMAGE_DIR = "AI156_images";
const RANGE_OUTPUT_DIR = path.join("output", "ranges");
const CLASSIFICATIONS = new Set(["biblical_text", "illustration", "blank", "non_biblical_text", "uncertain"]);
const CONFIDENCES = new Set(["high", "medium", "low"]);

const CLASSIFICATION_PROMPT = `Clasifica una página escaneada antes de extraerla.

Tu tarea NO es transcribir, NO es resolver versículos y NO es identificar referencias bíblicas exactas.
Solo decide si la imagen contiene texto bíblico útil visible.

Categorías válidas:
- biblical_text: contiene versículos bíblicos visibles o fragmentos bíblicos útiles.
- illustration: página principalmente visual/imagen sin versículos bíblicos útiles.
- blank: página vacía o sin contenido útil.
- non_biblical_text: contiene texto visible, pero no corresponde a versículos bíblicos útiles.
- uncertain: no hay suficiente certeza para clasificar.

Regla importante:
No clasifiques como no bíblica una página solo porque tenga título grande, inicio de libro, advertencia, encabezado, texto introductorio, transición de capítulo o fragmentos parciales. Si también hay versículos bíblicos visibles o fragmentos bíblicos útiles, clasifícala como biblical_text.

Sé conservador: si no puedes confirmar texto bíblico útil, usa uncertain.`;

function usage() {
  console.log("Uso: npm run extract-range -- <inicio> <fin>");
  console.log("Ejemplo: npm run extract-range -- AI156_0018 AI156_0050");
}

function parseImageToken(input) {
  const stem = imageStem(input);
  const match = stem.match(/^(.*?)(\d+)$/);
  if (!match) {
    throw new Error(`No se pudo interpretar el nombre de imagen como rango numérico: ${input}`);
  }
  return {
    stem,
    prefix: match[1],
    numberText: match[2],
    number: Number.parseInt(match[2], 10),
    width: match[2].length,
  };
}

function expandRange(fromInput, toInput) {
  const from = parseImageToken(fromInput);
  const to = parseImageToken(toInput);

  if (from.prefix !== to.prefix) {
    throw new Error(`Los extremos del rango no comparten prefijo: ${from.stem} / ${to.stem}`);
  }
  if (from.width !== to.width) {
    throw new Error(`Los extremos del rango no usan el mismo ancho numérico: ${from.stem} / ${to.stem}`);
  }
  if (from.number > to.number) {
    throw new Error(`El inicio del rango debe ser menor o igual al fin: ${from.stem} / ${to.stem}`);
  }

  const images = [];
  for (let current = from.number; current <= to.number; current += 1) {
    images.push(imageNameFromStem(`${from.prefix}${String(current).padStart(from.width, "0")}`));
  }
  return images;
}

async function assertImagesExist(images) {
  const missing = [];
  for (const image of images) {
    try {
      await fs.access(path.join(IMAGE_DIR, image));
    } catch {
      missing.push(path.join(IMAGE_DIR, image));
    }
  }
  if (missing.length > 0) {
    throw new Error(`No se encontraron imágenes del rango:\n${missing.join("\n")}`);
  }
}

async function hasValidPageJson(image) {
  const filePath = path.join("output", "pages_json", pageJsonName(image));
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    const parsed = JSON.parse(raw);
    if (parsed && parsed.image === image && Array.isArray(parsed.sections)) {
      return true;
    }
  } catch {
    // Se considera inexistente o inválido y se permite reextraer.
  }
  return false;
}

function validateClassification(payload, image) {
  const errors = [];
  if (!payload || typeof payload !== "object") errors.push("classification_payload_not_object");
  if (payload?.image !== image) errors.push("classification_image_mismatch");
  if (!CLASSIFICATIONS.has(payload?.classification)) errors.push("classification_invalid");
  if (!CONFIDENCES.has(payload?.confidence)) errors.push("confidence_invalid");
  if (typeof payload?.reason !== "string" || payload.reason.trim() === "") errors.push("reason_invalid");
  if (typeof payload?.requires_manual_review !== "boolean") errors.push("requires_manual_review_invalid");
  return { valid: errors.length === 0, errors };
}

function fallbackUncertain(image, reason) {
  return {
    image,
    classification: "uncertain",
    confidence: "low",
    reason,
    requires_manual_review: true,
  };
}

function normalizeClassification(payload, image) {
  const validation = validateClassification(payload, image);
  if (!validation.valid) {
    return fallbackUncertain(image, `Clasificación inválida: ${validation.errors.join(", ")}. No se pudo confirmar si contiene texto bíblico útil.`);
  }

  if (payload.confidence === "low" || payload.classification === "uncertain") {
    return {
      image,
      classification: "uncertain",
      confidence: payload.confidence,
      reason: payload.reason || "No se pudo confirmar si contiene texto bíblico útil.",
      requires_manual_review: true,
    };
  }

  return {
    image,
    classification: payload.classification,
    confidence: payload.confidence,
    reason: payload.reason,
    requires_manual_review: false,
  };
}

async function classifyPage({ client, image }) {
  try {
    const imageBuffer = await fs.readFile(path.join(IMAGE_DIR, image));
    const b64 = imageBuffer.toString("base64");
    const response = await client.responses.create({
      model: "gpt-5.4",
      input: [
        {
          role: "system",
          content: [{ type: "input_text", text: CLASSIFICATION_PROMPT }],
        },
        {
          role: "user",
          content: [
            {
              type: "input_text",
              text: `Clasifica esta página y devuelve únicamente JSON estructurado con image=${image}.`,
            },
            {
              type: "input_image",
              image_url: `data:image/jpeg;base64,${b64}`,
              detail: "low",
            },
          ],
        },
      ],
      text: {
        format: {
          type: "json_schema",
          name: "page_preclassification",
          schema: {
            type: "object",
            additionalProperties: false,
            required: ["image", "classification", "confidence", "reason", "requires_manual_review"],
            properties: {
              image: { type: "string" },
              classification: { type: "string", enum: [...CLASSIFICATIONS] },
              confidence: { type: "string", enum: [...CONFIDENCES] },
              reason: { type: "string" },
              requires_manual_review: { type: "boolean" },
            },
          },
        },
      },
    });
    return normalizeClassification(JSON.parse(response.output_text), image);
  } catch (error) {
    return fallbackUncertain(image, `No se pudo completar la clasificación previa: ${error.message}`);
  }
}

function runIndividualExtraction(image) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join("src", "extract.js"), image], {
      stdio: "inherit",
      env: process.env,
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`La extracción individual falló para ${image} con código ${code}.`));
    });
  });
}

function pageOutputs(image) {
  return {
    page_json: path.join("output", "pages_json", pageJsonName(image)),
    review: path.join("output", "review", reviewJsonName(image)),
  };
}

function skippedReason(classification) {
  if (classification === "illustration") return "Página principalmente ilustrativa sin versículos bíblicos visibles.";
  if (classification === "blank") return "Página vacía o sin contenido bíblico útil visible.";
  if (classification === "non_biblical_text") return "Página con texto visible, pero sin versículos bíblicos útiles.";
  return "No se pudo confirmar si contiene texto bíblico útil.";
}

async function processRange(images) {
  await assertImagesExist(images);
  await fs.mkdir(RANGE_OUTPUT_DIR, { recursive: true });

  const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
  const processed = [];
  const skipped = [];

  for (const image of images) {
    console.log(`\nClasificando ${image}...`);
    const classification = await classifyPage({ client, image });
    console.log(`Clasificación: ${classification.classification} (${classification.confidence})`);

    if (classification.classification !== "biblical_text") {
      skipped.push({
        image,
        classification: classification.classification,
        confidence: classification.confidence,
        status: "skipped",
        requires_manual_review: classification.requires_manual_review,
        reason: classification.reason || skippedReason(classification.classification),
      });
      continue;
    }

    if (await hasValidPageJson(image)) {
      processed.push({
        image,
        classification: classification.classification,
        confidence: classification.confidence,
        status: "already_extracted",
        outputs: pageOutputs(image),
      });
      console.log(`Ya existe salida válida: output/pages_json/${pageJsonName(image)}`);
      continue;
    }

    await runIndividualExtraction(image);
    processed.push({
      image,
      classification: classification.classification,
      confidence: classification.confidence,
      status: "extracted",
      outputs: pageOutputs(image),
    });
  }

  const summary = {
    range: {
      from: images[0],
      to: images[images.length - 1],
    },
    processed,
    skipped,
    metadata: {
      total: images.length,
      extracted: processed.filter((item) => item.status === "extracted").length,
      already_extracted: processed.filter((item) => item.status === "already_extracted").length,
      skipped: skipped.length,
      requires_manual_review: skipped.some((item) => item.requires_manual_review),
    },
  };

  const outputPath = path.join(RANGE_OUTPUT_DIR, rangeSummaryJsonName(images));
  await fs.writeFile(outputPath, `${JSON.stringify(summary, null, 2)}\n`, "utf-8");
  console.log(`\nResumen de rango: ${outputPath}`);
}

async function main() {
  const [fromInput, toInput] = process.argv.slice(2);
  if (!fromInput || !toInput) {
    usage();
    process.exit(1);
  }

  const images = expandRange(fromInput, toInput);
  await processRange(images);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
