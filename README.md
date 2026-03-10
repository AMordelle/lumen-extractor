# lumen-extractor

Extractor experimental para páginas de la Biblia Torres Amat usando **OpenAI Vision (vision-first)**.

## Objetivo de este PR

Validar una estrategia nueva basada en API (sin pipeline local OCR+regex como núcleo) para:
1. extraer estructura semántica de página,
2. extraer versículos bíblicos limpios,
3. medir tokens y costo estimado por página.

## Requisitos

- Python 3.10+
- Dependencias:

```bash
pip install -r requirements.txt
```

- API key en entorno o `.env`:

```bash
cp .env.example .env
# editar .env con OPENAI_API_KEY
```

## Entradas de prueba actuales

El script procesa por defecto estas 4 imágenes dentro de `AI156_images/`:

- `AI156_0018.jpg`
- `AI156_0020.jpg`
- `AI156_0074.jpg`
- `AI156_0257.jpg`

Se puede cambiar la lista con `--images`.

## Ejecución

```bash
python experimental_vision_extractor.py
```

Con opciones explícitas:

```bash
python experimental_vision_extractor.py \
  --input-dir AI156_images \
  --images AI156_0018.jpg AI156_0020.jpg AI156_0074.jpg AI156_0257.jpg \
  --resize-max-width 1600 \
  --resize-max-height 2300 \
  --model gpt-4.1 \
  --retry-attempts 1
```

## Salidas

El script crea automáticamente:

- `output/resized/` → imágenes redimensionadas enviadas a la API
- `output/pages_json/` → JSON por página
- `output/verses_json/` → JSON de versículos por página
- `output/logs/` → respuestas crudas, texto de respuesta y resumen de costos/tokens

### JSON de página esperado

```json
{
  "imagen_origen": "AI156_0018.jpg",
  "libro": "Génesis",
  "capitulo": 1,
  "tipo_pagina": "inicio_libro_con_texto_biblico",
  "pagina_mixta": true,
  "columnas_biblicas": 2,
  "tiene_notas_al_pie": true,
  "tiene_ornamento_central": true,
  "versiculo_inicio_visible": 1,
  "versiculo_fin_visible": 24,
  "observaciones": []
}
```

### JSON de versículos esperado

```json
[
  {
    "imagen_origen": "AI156_0018.jpg",
    "libro": "Génesis",
    "capitulo": 1,
    "versiculo": 11,
    "texto": "Dijo asimismo..."
  }
]
```

## Diseño implementado

- **Vision-first**: el prompt instruye al modelo a leer la página como documento (estructura y semántica), no como OCR plano.
- **Reglas de extracción**:
  - incluir páginas mixtas de inicio de libro,
  - excluir notas al pie,
  - excluir resúmenes/editorial,
  - ignorar ornamento central,
  - respetar lectura bíblica por columnas (izquierda→derecha).
- **Confiabilidad JSON**:
  - valida que el JSON sea parseable,
  - si falla, reintenta 1 vez con instrucción JSON estricta.
- **Costos**:
  - registra tokens por página (si la API los devuelve),
  - calcula costo estimado con tarifas configurables CLI,
  - guarda total de lote en `output/logs/batch_summary.json`.

## Nota

Este extractor es **experimental** para validación de calidad/costo y **no** escribe en SQLite.


## Postprocesado conservador (nueva etapa)

Esta iteración añade una etapa separada para limpiar **solo errores mecánicos evidentes** sobre archivos ya generados en `output/verses_json/`.

Ejecutar:

```bash
python conservative_postprocess.py
```

Entradas por defecto:
- `output/verses_json/AI156_0018.verses.json`
- `output/verses_json/AI156_0020.verses.json`
- `output/verses_json/AI156_0074.verses.json`
- `output/verses_json/AI156_0257.verses.json`

Salidas en `output/cleaned/` por imagen:
- `<imagen>.verses_raw.json` (copia del verso original)
- `<imagen>.verses_clean.json` (versos con limpieza conservadora)
- `<imagen>.cleaning_report.json` (solo versos que cambiaron)
- `<imagen>.warnings.json` (alertas estructurales, sin autocorrección)

Y un resumen de lote:
- `output/cleaned/cleaning_report.json`

### Limpiezas permitidas
- eliminar espacios dobles,
- quitar espacios incorrectos antes de `, . : ;`,
- asegurar un único espacio después de `, . : ;` cuando corresponda,
- corregir duplicación consecutiva de palabra idéntica con comparación literal (case-sensitive),
- limpiar solo caracteres basura aislados OCR (`|`, `~`, `` ` ``) cuando aparecen como token suelto.

### Restricciones (no se hace)
- no reemplazar letras,
- no sustituir palabras,
- no corregir ortografía,
- no modificar acentos,
- no unir ni dividir palabras,
- no modernizar ortografía,
- no reescribir frases,
- no completar palabras por inferencia,
- no cambiar estilo,
- no corregir semántica dudosa.

### Validaciones estructurales (solo advertencias)
- versículos duplicados dentro de una página,
- saltos de numeración sospechosos,
- versículos anormalmente largos.
