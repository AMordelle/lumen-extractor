import fs from "node:fs/promises";
import path from "node:path";
import OpenAI from "openai";

const PAGE_TYPES = new Set([
  "biblical_text",
  "book_start",
  "chapter_start",
  "mixed_biblical_and_notes",
  "non_biblical",
  "unknown",
]);

const AUDIT_SUSPICION_TYPES = new Set([
  "possible_added_word",
  "possible_replaced_word",
  "possible_context_completion",
  "possible_modernization",
  "possible_rewrite",
  "uncertain_visual_match",
]);

const SYSTEM_PROMPT = `Eres un transcriptor visual especializado en textos bíblicos antiguos escaneados.

Tu tarea es leer la imagen proporcionada y extraer únicamente el texto bíblico principal visible.

Reglas obligatorias:
1. Transcribe de forma fidedigna el texto principal.
2. No modernices palabras.
3. No corrijas estilo, ortografía antigua ni redacción.
4. No resumas.
5. No interpretes.
6. No completes texto por memoria bíblica.
7. Ignora notas al pie.
8. Ignora números pequeños de referencia insertados dentro del texto.
9. Ignora encabezados editoriales, pies de página, adornos y número de página.
10. No conserves formato visual como cursivas, negritas, tamaño de letra o tipografía.
11. Si una palabra no se distingue con seguridad, colócala en uncertain_words.
12. Si un versículo está cortado o incompleto por la imagen, marca is_partial como true.
13. Si no puedes identificar libro, capítulo o número de versículo, usa null.
14. Devuelve únicamente JSON válido usando la estructura solicitada.
15. NO completes frases parcialmente visibles aunque creas entender el contexto.
16. NO reemplaces palabras antiguas por versiones modernas o interpretadas.
17. Transcribe exactamente las palabras visibles incluso si parecen extrañas, incompletas o ambiguas.
18. Si no puedes identificar una palabra con suficiente certeza:
   - no la inventes;
   - no la reemplaces;
   - no la completes por contexto;
   - marca el versículo para revisión manual usando requires_review=true y explica la causa en review_notes.`;

const AUDIT_SYSTEM_PROMPT = `Eres un auditor visual de fidelidad documental.

Tu tarea NO es transcribir de nuevo.
Tu tarea es comparar la imagen original con el texto JSON extraído previamente.

Busca únicamente posibles diferencias peligrosas entre lo que se ve en la imagen y lo que fue extraído.

Debes marcar sospechas cuando detectes:
- palabras agregadas;
- palabras reemplazadas;
- frases completadas por contexto;
- modernizaciones;
- reescrituras;
- texto extraído que no parece estar realmente visible.

No marques diferencias menores de acentos o tipografía si la palabra base es la misma.
Ejemplos tolerables:
Crió / Criò
movia / movía
dia / día

Ejemplos sospechosos:
aquella / aquel dia
dióle / le dio
llamóle / llamó

No corrijas automáticamente.
No uses otra Biblia como referencia.
No completes por memoria.
Devuelve únicamente JSON válido con la estructura solicitada.`;

function usage() {
  console.log("Uso: npm run extract -- AI156_0005.jpg");
}

function validatePayload(payload, imageName) {
  const errors = [];
  if (!payload || typeof payload !== "object") errors.push("payload_not_object");
  if (payload.image !== imageName) errors.push("image_mismatch");
  if (!PAGE_TYPES.has(payload.page_type)) errors.push("invalid_page_type");
  if (!Array.isArray(payload.verses)) errors.push("verses_not_array");
  if (!Array.isArray(payload.ignored_elements)) errors.push("ignored_elements_not_array");
  if (!Array.isArray(payload.warnings)) errors.push("warnings_not_array");
  if (typeof payload.requires_manual_review !== "boolean") errors.push("requires_manual_review_invalid");
  if (!Array.isArray(payload.review_reasons)) errors.push("review_reasons_not_array");
  if (Array.isArray(payload.review_reasons)) {
    payload.review_reasons.forEach((reason, i) => {
      if (typeof reason !== "string") errors.push(`review_reasons_${i}_not_string`);
    });
  }

  if (Array.isArray(payload.verses)) {
    payload.verses.forEach((v, i) => {
      if (typeof v !== "object" || v === null) {
        errors.push(`verse_${i}_not_object`);
        return;
      }
      if (!(Number.isInteger(v.verse) || v.verse === null)) errors.push(`verse_${i}_verse_invalid`);
      if (typeof v.text !== "string") errors.push(`verse_${i}_text_invalid`);
      if (typeof v.is_partial !== "boolean") errors.push(`verse_${i}_is_partial_invalid`);
      if (!Array.isArray(v.uncertain_words)) errors.push(`verse_${i}_uncertain_words_invalid`);
      if (typeof v.requires_review !== "boolean") errors.push(`verse_${i}_requires_review_invalid`);
      if (!Array.isArray(v.review_notes)) errors.push(`verse_${i}_review_notes_invalid`);
      if (Array.isArray(v.uncertain_words)) {
        v.uncertain_words.forEach((word, j) => {
          if (typeof word !== "string") errors.push(`verse_${i}_uncertain_words_${j}_not_string`);
        });
      }
      if (Array.isArray(v.review_notes)) {
        v.review_notes.forEach((note, j) => {
          if (typeof note !== "string") errors.push(`verse_${i}_review_notes_${j}_not_string`);
        });
      }
    });
  }

  return { valid: errors.length === 0, errors };
}

function validateAuditPayload(payload, imageName) {
  const errors = [];
  if (!payload || typeof payload !== "object") errors.push("audit_payload_not_object");
  if (payload.image !== imageName) errors.push("audit_image_mismatch");
  if (!Array.isArray(payload.suspicions)) errors.push("audit_suspicions_not_array");

  if (Array.isArray(payload.suspicions)) {
    payload.suspicions.forEach((suspicion, i) => {
      if (!suspicion || typeof suspicion !== "object") {
        errors.push(`audit_suspicion_${i}_not_object`);
        return;
      }
      if (!(Number.isInteger(suspicion.verse) || suspicion.verse === null)) errors.push(`audit_suspicion_${i}_verse_invalid`);
      if (!AUDIT_SUSPICION_TYPES.has(suspicion.type)) errors.push(`audit_suspicion_${i}_type_invalid`);
      if (typeof suspicion.extracted_text !== "string") errors.push(`audit_suspicion_${i}_extracted_text_invalid`);
      if (typeof suspicion.suspected_original !== "string") errors.push(`audit_suspicion_${i}_suspected_original_invalid`);
      if (typeof suspicion.reason !== "string") errors.push(`audit_suspicion_${i}_reason_invalid`);
    });
  }

  return { valid: errors.length === 0, errors };
}

function buildReviewItems(payload, auditPayload = null, auditWarning = null) {
  const items = [];

  (payload.warnings || []).forEach((warning) => {
    items.push({
      verse: null,
      reason: `Warning de página: ${warning}`,
      text: "",
    });
  });

  (payload.verses || []).forEach((v) => {
    if (v.is_partial) {
      items.push({
        verse: v.verse,
        reason: "Versículo incompleto por corte o visibilidad parcial.",
        text: v.text,
      });
    }

    if ((v.uncertain_words || []).length > 0) {
      items.push({
        verse: v.verse,
        reason: `Palabras inciertas detectadas: ${v.uncertain_words.join(", ")}`,
        text: v.text,
      });
    }

    if (v.requires_review) {
      items.push({
        verse: v.verse,
        reason: "El modelo marcó requires_review=true para este versículo.",
        text: v.text,
      });
    }

    (v.review_notes || []).forEach((note) => {
      items.push({
        verse: v.verse,
        reason: `Nota de revisión: ${note}`,
        text: v.text,
      });
    });
  });

  if (auditWarning) {
    items.push({
      verse: null,
      reason: `Warning de auditoría visual: ${auditWarning}`,
      text: "",
    });
  }

  (auditPayload?.suspicions || []).forEach((suspicion) => {
    const matchedVerse = (payload.verses || []).find((v) => v.verse === suspicion.verse);
    items.push({
      verse: suspicion.verse,
      reason: `Auditor visual: ${suspicion.type} - ${suspicion.reason}`,
      text: matchedVerse?.text || suspicion.extracted_text,
    });
  });

  return items;
}

function buildAuditInput(parsed) {
  return {
    image: parsed.image,
    verses: (parsed.verses || []).map((v) => ({ verse: v.verse, text: v.text })),
  };
}

async function runVisualAudit({ client, imageName, b64, parsed }) {
  const response = await client.responses.create({
    model: "gpt-5.4",
    input: [
      {
        role: "system",
        content: [{ type: "input_text", text: AUDIT_SYSTEM_PROMPT }],
      },
      {
        role: "user",
        content: [
          {
            type: "input_text",
            text: `Compara esta imagen contra el JSON extraído y devuelve solo sospechas válidas:\n${JSON.stringify(buildAuditInput(parsed))}`,
          },
          {
            type: "input_image",
            image_url: `data:image/jpeg;base64,${b64}`,
            detail: "high",
          },
        ],
      },
    ],
    text: {
      format: {
        type: "json_schema",
        name: "visual_fidelity_audit",
        schema: {
          type: "object",
          additionalProperties: false,
          required: ["image", "suspicions"],
          properties: {
            image: { type: "string" },
            suspicions: {
              type: "array",
              items: {
                type: "object",
                additionalProperties: false,
                required: ["verse", "type", "extracted_text", "suspected_original", "reason"],
                properties: {
                  verse: { type: ["integer", "null"] },
                  type: { type: "string", enum: [...AUDIT_SUSPICION_TYPES] },
                  extracted_text: { type: "string" },
                  suspected_original: { type: "string" },
                  reason: { type: "string" },
                },
              },
            },
          },
        },
      },
    },
  });

  const parsedAudit = JSON.parse(response.output_text);
  return { response, parsedAudit };
}

async function main() {
  const imageName = process.argv[2];
  if (!imageName) {
    usage();
    process.exit(1);
  }

  const inputPath = path.join("AI156_images", imageName);
  const imageBuffer = await fs.readFile(inputPath);
  const b64 = imageBuffer.toString("base64");

  const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

  const response = await client.responses.create({
    model: "gpt-5.4",
    input: [
      {
        role: "system",
        content: [{ type: "input_text", text: SYSTEM_PROMPT }],
      },
      {
        role: "user",
        content: [
          {
            type: "input_text",
            text: `Extrae esta página y devuelve JSON con image=${imageName}.`,
          },
          {
            type: "input_image",
            image_url: `data:image/jpeg;base64,${b64}`,
            detail: "high",
          },
        ],
      },
    ],
    text: {
      format: {
        type: "json_schema",
        name: "biblical_page_extraction",
        schema: {
          type: "object",
          additionalProperties: false,
          required: [
            "image",
            "page_type",
            "book",
            "chapter",
            "verses",
            "ignored_elements",
            "warnings",
            "requires_manual_review",
            "review_reasons",
          ],
          properties: {
            image: { type: "string" },
            page_type: { type: "string", enum: [...PAGE_TYPES] },
            book: { type: ["string", "null"] },
            chapter: { type: ["integer", "null"] },
            verses: {
              type: "array",
              items: {
                type: "object",
                additionalProperties: false,
                required: ["verse", "text", "is_partial", "uncertain_words", "requires_review", "review_notes"],
                properties: {
                  verse: { type: ["integer", "null"] },
                  text: { type: "string" },
                  is_partial: { type: "boolean" },
                  uncertain_words: { type: "array", items: { type: "string" } },
                  requires_review: { type: "boolean" },
                  review_notes: { type: "array", items: { type: "string" } },
                },
              },
            },
            ignored_elements: { type: "array", items: { type: "string" } },
            warnings: { type: "array", items: { type: "string" } },
            requires_manual_review: { type: "boolean" },
            review_reasons: { type: "array", items: { type: "string" } },
          },
        },
      },
    },
  });

  await fs.mkdir("output/raw_responses", { recursive: true });
  await fs.mkdir("output/pages_json", { recursive: true });
  await fs.mkdir("output/audit", { recursive: true });
  await fs.mkdir("output/review", { recursive: true });

  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const base = `${path.parse(imageName).name}_${stamp}`;

  await fs.writeFile(`output/raw_responses/${base}.json`, JSON.stringify(response, null, 2), "utf-8");

  const parsed = JSON.parse(response.output_text);
  const validation = validatePayload(parsed, imageName);
  if (!validation.valid) {
    throw new Error(`JSON inválido tras validación local: ${validation.errors.join(", ")}`);
  }

  await fs.writeFile(`output/pages_json/${base}.json`, JSON.stringify(parsed, null, 2), "utf-8");

  let auditPayload = null;
  let auditWarning = null;
  try {
    const { response: auditResponse, parsedAudit } = await runVisualAudit({ client, imageName, b64, parsed });
    await fs.writeFile(`output/audit/${base}.json`, JSON.stringify(auditResponse, null, 2), "utf-8");

    const auditValidation = validateAuditPayload(parsedAudit, imageName);
    if (!auditValidation.valid) {
      auditWarning = `Respuesta inválida del auditor: ${auditValidation.errors.join(", ")}`;
    } else {
      auditPayload = parsedAudit;
    }
  } catch (error) {
    auditWarning = `No se pudo completar la auditoría visual: ${error.message}`;
  }

  const reviewItems = buildReviewItems(parsed, auditPayload, auditWarning);
  if (parsed.requires_manual_review || reviewItems.length > 0) {
    const reviewPayload = {
      image: imageName,
      requires_manual_review: true,
      items: reviewItems,
    };
    await fs.writeFile(`output/review/${base}.json`, JSON.stringify(reviewPayload, null, 2), "utf-8");
  }

  console.log(`Extracción completada: ${imageName}`);
  console.log(`Raw: output/raw_responses/${base}.json`);
  console.log(`Validado: output/pages_json/${base}.json`);
  if (auditPayload) console.log(`Auditoría: output/audit/${base}.json`);
  if (auditWarning) console.log(`Auditoría con warning: ${auditWarning}`);
  if (parsed.requires_manual_review || reviewItems.length > 0) console.log(`Revisión: output/review/${base}.json`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
