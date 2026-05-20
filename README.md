# Lumen Extractor (Prototipo 1)

Extractor visual de texto bíblico desde imágenes escaneadas usando **OpenAI Responses API** con visión (sin OCR tradicional).

## Configuración

1. Instala dependencias:

```bash
npm install
```

2. Configura tu clave:

```bash
export OPENAI_API_KEY="tu_api_key"
```

## Ejecutar una imagen

```bash
npm run extract -- AI156_0005.jpg
```

El script procesa **una sola imagen por ejecución** y la busca dentro de `AI156_images/`.

## Flujo actual

1. Primera pasada: extracción visual estructurada del texto bíblico.
2. Validación local estricta del JSON extraído.
3. Segunda pasada: **auditor visual de fidelidad documental**.
4. Integración de hallazgos en `output/review/` sin corregir automáticamente el contenido extraído.

## Salidas

Se generan archivos en:

- `output/raw_responses/`: respuesta cruda completa de la extracción.
- `output/pages_json/`: JSON validado localmente de la extracción.
- `output/audit/`: respuesta estructurada del auditor visual de fidelidad.
- `output/review/`: resumen de revisión manual con incidencias del extractor y sospechas del auditor.

## Auditor visual de fidelidad documental

El auditor visual es una **segunda pasada** con OpenAI. Su objetivo es comparar:

- la imagen original;
- el JSON ya extraído (versículos).

El auditor **no reextrae**, **no corrige automáticamente** y **no modifica** `output/pages_json/`.
Solo reporta sospechas de fidelidad documental (por ejemplo palabras añadidas, reemplazadas, modernizadas o frases completadas por contexto) para revisión humana.

Tipos de sospecha soportados:

- `possible_added_word`
- `possible_replaced_word`
- `possible_context_completion`
- `possible_modernization`
- `possible_rewrite`
- `uncertain_visual_match`

Si la auditoría falla o devuelve JSON inválido, el proceso principal no se rompe: se agrega un warning en `output/review/`.

## Campos de revisión

### `requires_manual_review` (nivel página)

- `true`: la página completa requiere revisión humana.
- `false`: no se detectaron señales suficientes para forzar revisión manual de página.

Se complementa con `review_reasons` (array de strings) para explicar por qué la página se marcó.

### `requires_review` (nivel versículo)

- `true`: ese versículo requiere inspección humana puntual.
- `false`: no se marcó ese versículo para revisión.

Se complementa con `review_notes` (array de strings) para documentar el motivo concreto por versículo.

## Cómo interpretar `output/review/`

El archivo de `output/review/` es un resumen operativo para revisión humana. Incluye:

- `image`
- `requires_manual_review`
- `items`: lista de incidencias con `verse`, `reason` y `text`.

Se agregan `items` cuando ocurre cualquiera de estos casos:

- existen `warnings` en la página;
- un versículo tiene `is_partial = true`;
- un versículo tiene `uncertain_words`;
- un versículo tiene `requires_review = true`;
- un versículo incluye `review_notes`;
- el auditor visual detecta sospechas;
- la auditoría visual no pudo completarse o respondió de forma inválida.

## Limitaciones actuales

- No hace procesamiento masivo.
- No usa base de datos ni interfaz gráfica.
- No exporta PDF.
- No une capítulos/páginas.
- No hace corrección bíblica avanzada ni comparación con otras Biblias.
- No devuelve bounding boxes ni coordenadas visuales.
