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
18. Tu única función es transcribir palabra por palabra lo visible.
19. Si una palabra no se ve claramente:
   - no la reemplaces;
   - no la completes;
   - no la modernices;
   - no uses contexto visual o lingüístico para deducirla;
   - no conviertas una palabra dudosa en varias palabras más comprensibles.
20. Si no puedes identificar una palabra con suficiente certeza:
   - conserva la lectura más literal posible sin reinterpretar;
   - colócala también en uncertain_words;
   - marca el versículo para revisión manual usando requires_review=true;
   - explica la incertidumbre visual en review_notes;
   - continúa con el siguiente versículo.
21. Prefiere incertidumbre antes que reinterpretación.
22. Marca requires_review=true cuando exista palabra borrosa, parcialmente visible, comprimida, deformada, antigua difícil de distinguir, lectura ambigua o duda razonable entre lecturas posibles.
23. No marques un versículo para revisión únicamente por diferencias menores de acentuación, inclinación del acento, diéresis, puntuación menor, mayúsculas/minúsculas o tipografía, siempre que la palabra base visible sea la misma.
24. La revisión debe activarse cuando exista duda sobre la palabra base, no sobre detalles gráficos menores.`;

const AUDIT_SYSTEM_PROMPT = `Compara visualmente el texto extraído contra la imagen palabra por palabra.

Tu única responsabilidad es detectar si la secuencia visible de palabras deja de coincidir.

En cuanto detectes una discrepancia relevante:
- detente;
- marca el versículo para revisión manual;
- continúa con el siguiente versículo.

NO intentes:
- explicar el error;
- clasificar el error;
- corregir el error;
- reinterpretar el texto;
- completar contexto.

Antes de comparar palabras, aplica normalización ligera tanto al texto extraído como a la lectura visual:
- convertir a minúsculas;
- ignorar variaciones diacríticas (acentos, inclinación del acento y diéresis);
- ignorar puntuación menor;
- ignorar variaciones tipográficas o de estilo visual;
- ignorar partículas o conectores equivalentes simples cuando no alteren la lectura principal.

NO hagas:
- stemming;
- lematización;
- NLP complejo;
- reinterpretación;
- corrección automática.

Regla principal:
- ignora diferencias menores que no cambien la palabra base visible;
- ignora variaciones diacríticas, tipográficas, de puntuación menor o de partículas equivalentes que no alteren la lectura principal;
- marca revisión solo cuando la palabra base o la secuencia principal de palabras deje de coincidir.

Lo importante es detectar solo discrepancias relevantes: pérdida de palabras, reemplazos importantes, palabras agregadas, reinterpretaciones y cambios visibles relevantes.

Ejemplos ilustrativos (entre otros casos similares):
- no marcar diferencias equivalentes de lectura principal aunque cambie la forma diacrítica o tipográfica;
- sí marcar cuando una palabra base visible cambia o cuando se pierde/añade parte relevante de la secuencia.

Devuelve únicamente JSON válido con la estructura solicitada.`;

const RISK_WARNING_PATTERNS = [
  /texto\s+cortad/i,
  /cortad[ao]/i,
  /baja\s+legibilidad/i,
  /legibilidad\s+baja/i,
  /fragmento\s+parcial/i,
  /parcial/i,
  /transici[oó]n\s+compleja\s+de\s+p[aá]gina/i,
  /transici[oó]n\s+de\s+p[aá]gina/i,
  /p[aá]gina\s+compleja/i,
];

function isRiskWarning(warning) {
  if (typeof warning !== "string") return false;
  return RISK_WARNING_PATTERNS.some((pattern) => pattern.test(warning));
}

function getRiskVerses(payload) {
  return (payload.verses || []).filter((v) => {
    return v.is_partial || (v.uncertain_words || []).length > 0 || v.requires_review || (v.review_notes || []).length > 0;
  });
}

function buildRiskAuditInput(parsed) {
  const riskyWarnings = (parsed.warnings || []).filter((warning) => isRiskWarning(warning));
  const riskyVerses = getRiskVerses(parsed);

  return {
    image: parsed.image,
    context: {
      warnings: riskyWarnings,
      risk_summary: {
        verses_count: riskyVerses.length,
        warnings_count: riskyWarnings.length,
      },
    },
    verses: riskyVerses.map((v) => ({
      verse: v.verse,
      text: v.text,
      is_partial: v.is_partial,
      uncertain_words: v.uncertain_words,
      requires_review: v.requires_review,
      review_notes: v.review_notes,
    })),
  };
}

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
      if (typeof suspicion.text !== "string") errors.push(`audit_suspicion_${i}_text_invalid`);
      if (typeof suspicion.reason !== "string") errors.push(`audit_suspicion_${i}_reason_invalid`);
    });
  }

  return { valid: errors.length === 0, errors };
}

function buildReviewItems(payload, auditPayload = null, auditWarning = null) {
  const groups = new Map();

  function ensureGroup(verse, text = "") {
    const key = verse === null ? "null" : String(verse);
    if (!groups.has(key)) {
      groups.set(key, {
        verse,
        reasons: [],
        text,
      });
    } else if (!groups.get(key).text && text) {
      groups.get(key).text = text;
    }
    return groups.get(key);
  }

  function addReason(verse, reason, text = "") {
    const group = ensureGroup(verse, text);
    if (!group.reasons.includes(reason)) group.reasons.push(reason);
  }

  (payload.warnings || []).forEach((warning) => {
    addReason(null, `Warning de página: ${warning}`, "");
  });

  (payload.verses || []).forEach((v) => {
    if (v.is_partial) addReason(v.verse, "Versículo incompleto por corte o visibilidad parcial.", v.text);
    if ((v.uncertain_words || []).length > 0) addReason(v.verse, `Palabras inciertas detectadas: ${v.uncertain_words.join(", ")}`, v.text);
    if (v.requires_review) addReason(v.verse, "El modelo marcó requires_review=true para este versículo.", v.text);
    (v.review_notes || []).forEach((note) => addReason(v.verse, `Nota de revisión: ${note}`, v.text));
  });

  if (auditWarning) addReason(null, `Warning de auditoría visual: ${auditWarning}`, "");

  (auditPayload?.suspicions || []).forEach((suspicion) => {
    const matchedVerse = (payload.verses || []).find((v) => v.verse === suspicion.verse);
    addReason(suspicion.verse, `Auditor visual: ${suspicion.reason}`, matchedVerse?.text || suspicion.text);
  });

  return Array.from(groups.values());
}

async function runVisualAudit({ client, imageName, b64, parsed }) {
  const auditInput = buildRiskAuditInput(parsed);
  if (auditInput.verses.length === 0 && auditInput.context.warnings.length === 0) {
    return { response: null, parsedAudit: { image: imageName, suspicions: [] }, skipped: true };
  }

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
            text: `Compara esta imagen contra el JSON extraído y devuelve solo sospechas válidas:\n${JSON.stringify(buildRiskAuditInput(parsed))}`,
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
                required: ["verse", "text", "reason"],
                properties: {
                  verse: { type: ["integer", "null"] },
                  text: { type: "string" },
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
  return { response, parsedAudit, skipped: false };
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
    const { response: auditResponse, parsedAudit, skipped } = await runVisualAudit({ client, imageName, b64, parsed });
    if (!skipped && auditResponse) {
      await fs.writeFile(`output/audit/${base}.json`, JSON.stringify(auditResponse, null, 2), "utf-8");
    }

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
  if (auditPayload) {
    if ((auditPayload.suspicions || []).length > 0) {
      console.log(`Auditoría dirigida: output/audit/${base}.json`);
    } else {
      console.log("Auditoría dirigida sin sospechas adicionales.");
    }
  }
  if (auditWarning) console.log(`Auditoría con warning: ${auditWarning}`);
  if (parsed.requires_manual_review || reviewItems.length > 0) console.log(`Revisión: output/review/${base}.json`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
