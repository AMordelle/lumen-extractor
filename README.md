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

## Esquema estructural por secciones (`sections[]`)

Ahora la salida principal está diseñada para páginas que pueden contener transición de capítulo en una misma imagen.

En lugar de depender de `book`, `chapter` y `verses` a nivel raíz, el JSON usa:

- `sections[]` (fuente principal)
- cada sección incluye su propio:
  - `book` (`string|null`)
  - `chapter` (`int|null`)
  - `verses[]`

Esto permite modelar correctamente casos como:

- cierre de un capítulo y comienzo del siguiente en la misma página;
- fragmentos bíblicos parciales sin numeración visible;
- continuidad desde página anterior y hacia página siguiente.
- transición basada en coherencia estructural incluso cuando el encabezado visual sea ambiguo.

### Estructura resumida

```json
{
  "image": "AI156_0019.jpg",
  "page_type": "mixed_biblical_and_notes",
  "sections": [
    {
      "book": "Génesis",
      "chapter": 1,
      "verses": [
        {
          "verse": 25,
          "text": "...",
          "is_partial": false,
          "position": "complete_on_page",
          "uncertain_words": [],
          "requires_review": false,
          "review_notes": []
        }
      ]
    }
  ]
}
```

Si no se puede determinar con certeza el libro o capítulo, se usa `null` en el campo correspondiente de esa sección.

## Coherencia estructural de capítulos (PR9.1)

La detección de capítulos **no depende solo de encabezados visuales**. El extractor combina:

- secuencia de versículos;
- reinicios de numeración (`verse=1`);
- continuidad narrativa/documental;
- encabezados visibles como evidencia secundaria.

Reglas operativas:

- un reinicio `verse=1` dentro de una misma sección se trata como señal fuerte de posible nueva sección/capítulo;
- retrocesos de versículo (ej. `7:6 → 7:1`, `5:15 → 5:3`) se marcan como inconsistencias estructurales;
- si la estructura no permite determinar capítulo con confianza, se prefiere `chapter=null` antes que inventar capítulo;
- incoherencias significativas generan warnings estructurales y fuerzan `requires_manual_review=true`.

## Campo `position` por versículo

Cada versículo/framento debe incluir `position` con uno de estos valores:

- `complete_on_page`
- `starts_on_page`
- `continues_from_previous_page`
- `continues_on_next_page`
- `fragment_without_visible_number`

Uso operativo:

- fragmento al inicio sin numeración visible: `verse=null` y `position=fragment_without_visible_number` o `continues_from_previous_page`;
- versículo cortado al final: `is_partial=true` y `position=continues_on_next_page`.

Regla adicional importante:

- si hay fragmento bíblico visible al inicio de página, debe representarse estructuralmente en `sections[].verses[]` (no solo como warning), sin completar texto faltante.

## Salidas

Se generan archivos en:

- `output/raw_responses/`: respuesta cruda completa de la extracción.
- `output/pages_json/`: JSON validado localmente de la extracción.
- `output/audit/`: respuesta estructurada del auditor visual de fidelidad.
- `output/review/`: resumen de revisión manual con incidencias del extractor y sospechas del auditor.

## Auditor visual de fidelidad documental

El auditor visual es una **segunda pasada dirigida** con OpenAI.

Ahora el extractor decide qué auditar: el auditor recibe solo un subconjunto mínimo (no la página completa) compuesto por:

- versículos (en `sections[].verses[]`) con `is_partial = true`;
- versículos con `uncertain_words` no vacío;
- versículos con `requires_review = true`;
- versículos con `review_notes` no vacío;
- warnings de página relacionados con corte de texto, baja legibilidad, fragmento parcial o transición compleja de página.

El auditor **no reextrae**, **no corrige automáticamente** y **no modifica** `output/pages_json/`.
Solo actúa como segunda validación visual sobre zonas ya marcadas como riesgosas para reducir falsos positivos y ruido.

Si no hay señales de riesgo, la auditoría se omite y se continúa el flujo sin error.
Si la auditoría falla o devuelve JSON inválido, el proceso principal no se rompe: se agrega un warning en `output/review/`.

## Política estricta de incertidumbre visual

El extractor prioriza la **fidelidad visual documental** por encima de la legibilidad interpretada.

Principios operativos:

- transcribir palabra por palabra lo visible;
- no adivinar ni completar palabras ambiguas por contexto;
- no modernizar grafías antiguas ni reinterpretar construcciones dudosas;
- marcar la incertidumbre en lugar de resolverla automáticamente.

Cuando exista incertidumbre visual (palabra borrosa, parcial, comprimida, deformada, antigua difícil de distinguir o ambigua), el versículo debe marcarse con:

- `requires_review = true`
- `review_notes` explicando la causa visual de la duda.

El sistema **prefiere revisión humana** antes que reinterpretación automática.

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
- `items`: lista de incidencias con `book`, `chapter`, `verse`, `reasons` y `text`.

Notas importantes:

- El generador de review recorre `sections[].verses[]`.
- Si dos elementos tienen el mismo `verse` pero distinto `chapter`, se tratan como incidencias distintas.
- Warnings generales de página se representan con `book/chapter/verse = null`.

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
