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


## Nomenclatura de archivos

A partir de PR13, todos los artefactos generados usan nombres cortos, estables y predecibles. La raíz de una página es el identificador de la imagen sin extensión, por ejemplo `AI156_0018`. La raíz documental de un lote siempre usa la primera y la última página efectiva del proceso, separadas por doble guion bajo: `AI156_0018__AI156_0032`.

Convención por carpeta:

- `output/pages_json/`: una página validada por archivo, por ejemplo `AI156_0018.json`, `AI156_0019.json`, `AI156_0020.json`.
- `output/review/`: resumen de revisión manual por página, por ejemplo `AI156_0018.review.json` o `AI156_0019.review.json`.
- `output/audit/`: respuesta del auditor visual solo cuando la auditoría se ejecuta, por ejemplo `AI156_0018.audit.json` o `AI156_0019.audit.json`.
- `output/continuity/`: continuidad de un lote, por ejemplo `AI156_0018__AI156_0032.json`.
- `output/document/`: documento ensamblado del mismo lote, con la misma raíz visual que continuidad, por ejemplo `AI156_0018__AI156_0032.json`.
- `output/export/`: exportaciones legibles con la misma raíz documental, por ejemplo `AI156_0018__AI156_0032.md` y `AI156_0018__AI156_0032.txt`. En el futuro podrá añadirse `AI156_0018__AI156_0032.pdf`.

No se agregan timestamps, hashes, cantidad de páginas ni sufijos redundantes a los nombres de salida. Si un proceso recibe varias páginas, el rango canónico se calcula exclusivamente con la primera y la última página del lote (`AI156_0018 AI156_0019 AI156_0020 AI156_0021` → `AI156_0018__AI156_0021`).

Los comandos siguen aceptando rutas explícitas y nombres base simples. Cuando es razonable, las etapas derivadas conservan compatibilidad de lectura con archivos existentes de convenciones anteriores, pero las nuevas salidas se escriben con la nomenclatura PR13.

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


## Constructor de continuidad documental entre páginas (PR10)

Después de extraer páginas individuales, puedes construir una capa derivada de continuidad entre páginas consecutivas **sin modificar** los JSON originales en `output/pages_json/`.

El constructor:

- lee JSON ya validados basados en `sections[].verses[]`;
- busca señales estructurales de continuidad entre pares consecutivos de páginas;
- detecta fragmentos de cierre (`continues_on_next_page` o `is_partial=true`) y fragmentos de apertura (`continues_from_previous_page` o `fragment_without_visible_number`);
- genera un archivo derivado en `output/continuity/` con `images[]` y `connections[]`;
- es conservador: si no hay señales claras no crea conexión;
- si hay señales parciales, crea conexión con `confidence="low"` y `requires_manual_review=true`.
- permite continuidad cuando el fragmento inicial de la página siguiente tiene `verse=null` si la señal estructural es clara;
- en esos casos hereda referencia documental (`book`, `chapter`, `verse`) desde el fragmento final anterior para la conexión derivada;
- normaliza `book` solo para comparación (case-insensitive, sin acentos), sin modificar los textos originales de salida.

### Comando

```bash
npm run continuity -- AI156_0018 AI156_0019
```

También acepta rutas completas a JSON:

```bash
npm run continuity -- output/pages_json/AI156_0018.json output/pages_json/AI156_0019.json
```

Y múltiples páginas consecutivas:

```bash
npm run continuity -- AI156_0018 AI156_0019 AI156_0020 AI156_0021
```

### Salida

Se crea `output/continuity/<primera>__<ultima>.json` con la forma:

- `images`: lista de imágenes procesadas en orden;
- `connections`: conexiones detectadas entre páginas consecutivas.

Cada conexión incluye:

- `from_image`, `to_image`;
- `book`, `chapter`, `verse`;
- `previous_fragment`, `next_fragment`;
- `resolved_text` (incluye unión de palabra cortada por guion cuando aplique);
- `confidence`, `requires_manual_review`, `notes`.

### Unión de palabras cortadas por guion

Si el fragmento anterior termina con guion (ej. `vivien-`) y el siguiente inicia con continuación (ej. `tes ...`), se resuelve como `vivientes ...`:

- se elimina el guion final del fragmento previo;
- se une directamente con la primera palabra del siguiente fragmento;
- se conserva intacto el resto del texto (sin modernizar ni corregir ortografía).


## Constructor de documento bíblico continuo (PR11)

`build-document` construye una **capa derivada y conservadora** de documento continuo a partir de extracción y continuidad, sin modificar fuentes originales.

Diferencias de capas:

- **Extracción** (`output/pages_json/`): fuente primaria, estructura base por `sections[]`.
- **Continuidad** (`output/continuity/`): capa secundaria de conexiones entre páginas consecutivas con `resolved_text`.
- **Documento** (`output/document/`): ensamblado final por `book -> chapter -> verse`, preservando texto original y trazabilidad.

Principios del ensamblado:

- prioridad estructural de `output/pages_json/`;
- uso de continuidad solo cuando hay conexión explícita válida;
- agrupa libros usando clave normalizada (case-insensitive, sin acentos y sin espacios redundantes) para evitar dividir el mismo libro en múltiples bloques;
- la normalización de `book` aplica solo a comparación/agrupación documental; no altera texto bíblico ni JSON fuente;
- el nombre de salida del libro se mantiene estable/canónico dentro del documento (primera forma válida o alias interno).
- sin reinterpretar, modernizar, corregir ortografía ni completar texto faltante;
- fragmentos sin continuidad válida permanecen explícitos y generan warnings.
- si pasas páginas/JSON, intenta cargar automáticamente archivos compatibles desde `output/continuity/`;
- si pasas archivos de continuidad explícitos, los aplica de forma prioritaria y también puede complementar con compatibles del conjunto procesado.

### Comando

```bash
npm run build-document -- <inputs>
```

Entradas válidas:

- identificadores de página (`AI156_0018`);
- rutas de JSON de páginas (`output/pages_json/AI156_0018.json`);
- archivos de continuidad (`output/continuity/AI156_0018__AI156_0020.json`).

Ejemplos:

```bash
npm run build-document -- AI156_0018 AI156_0019 AI156_0020
npm run build-document -- output/pages_json/AI156_0018.json output/pages_json/AI156_0019.json
npm run build-document -- output/continuity/AI156_0018__AI156_0020.json
```

### Salida

Se genera en `output/document/` un JSON con:

- `books[]` agrupado por libro/capítulo/versículo;
- versículos con `text` final y `sources[]` mínimas (`image`, `position`);
- `metadata` con:
  - `generated_from`;
  - `continuity_files`;
  - `warnings`;
  - `requires_manual_review`.

Validaciones documentales incorporadas:

- duplicados de versículo;
- continuidad contradictoria;
- fragmentos parciales sin continuidad resuelta;
- continuidad aplicada con revisión pendiente (`confidence="low"` o `requires_manual_review=true`);
- alertas que requieren revisión manual.


## Exportación legible del documento bíblico continuo (PR12)

`export-document` genera una **vista legible para humanos** a partir de un documento continuo ya ensamblado en `output/document/`. Esta capa es derivada: no modifica `output/document/`, `output/pages_json/` ni `output/continuity/`.

Diferencias de capas:

- **Documento** (`output/document/`): JSON estructurado por `book -> chapter -> verse`, con metadata y trazabilidad mínima para procesamiento.
- **Exportación** (`output/export/`): representación de lectura en formatos simples, pensada para revisar el texto como un documento bíblico normal.

Principios de conservación textual:

- no reinterpreta texto;
- no corrige ortografía;
- no moderniza lenguaje;
- no altera puntuación, acentos, mayúsculas/minúsculas ni palabras antiguas;
- conserva los versículos parciales tal como quedaron en el documento continuo, sin inventar contenido;
- mantiene integradas las continuidades ya resueltas por la capa documental.

### Comando

```bash
npm run export-document -- <document-json>
```

Acepta rutas a documentos JSON, por ejemplo:

```bash
npm run export-document -- output/document/AI156_0018__AI156_0032.json
```

También puede recibir un nombre base compatible con `output/document/`:

```bash
npm run export-document -- AI156_0018__AI156_0032
```

### Salida

Crea `output/export/` si no existe y genera dos archivos con el mismo nombre base del documento:

- `output/export/<documento>.md`
- `output/export/<documento>.txt`

El Markdown usa encabezados de libro y capítulo, por ejemplo `# Génesis` y `## Capítulo 1`, seguido por versículos numerados. El TXT usa una forma simple con títulos en mayúsculas, por ejemplo `GÉNESIS` y `CAPÍTULO 1`.

Si `metadata.requires_manual_review=true` o existen warnings documentales, la exportación agrega una sección final de **Revisión pendiente**. Esta sección queda separada del cuerpo bíblico principal para no contaminar el texto exportado. También se agrega una sección breve de trazabilidad con páginas fuente, continuidad usada cuando exista y fecha de generación.

## Limitaciones actuales

- No hace procesamiento masivo.
- No usa base de datos ni interfaz gráfica.
- No exporta PDF.
- No hace corrección bíblica avanzada ni comparación con otras Biblias.
- No devuelve bounding boxes ni coordenadas visuales.
