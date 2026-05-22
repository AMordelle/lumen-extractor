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
3. Detección de señales de riesgo del extractor (versículos y warnings).
4. Segunda pasada: **auditor visual dirigido** solo sobre segmentos riesgosos.
5. Integración de hallazgos en `output/review/` sin corregir automáticamente el contenido extraído.

## Salidas

Se generan archivos en:

- `output/raw_responses/`: respuesta cruda completa de la extracción.
- `output/pages_json/`: JSON validado localmente de la extracción.
- `output/audit/`: respuesta estructurada del auditor visual de fidelidad.
- `output/review/`: resumen de revisión manual con incidencias del extractor y sospechas del auditor.

## Auditor visual de fidelidad documental

El auditor visual es una **segunda pasada dirigida** con OpenAI.

Ahora el extractor decide qué auditar: el auditor recibe solo un subconjunto mínimo (no la página completa) compuesto por:

- versículos con `is_partial = true`;
- versículos con `uncertain_words` no vacío;
- versículos con `requires_review = true`;
- versículos con `review_notes` no vacío;
- warnings de página relacionados con corte de texto, baja legibilidad, fragmento parcial o transición compleja de página.

El auditor **no reextrae**, **no corrige automáticamente** y **no modifica** `output/pages_json/`.
Solo actúa como segunda validación visual sobre zonas ya marcadas como riesgosas para reducir falsos positivos y ruido.

Si no hay señales de riesgo, la auditoría se omite y se continúa el flujo sin error.
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
