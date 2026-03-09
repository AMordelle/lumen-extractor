# lumen-extractor

Extractor experimental **vision-first** para páginas escaneadas de la Biblia Torres Amat (Tomo 1).

## Enfoque

Este extractor prioriza la comprensión visual del documento:

1. Detección de layout por bloques en toda la página.
2. Clasificación semántica de zonas (`titulo_libro`, `titulo_capitulo`, `resumen_capitulo`, `cuerpo_biblico`, `notas_al_pie`, `ornamento_central`).
3. Inferencia de metadatos de página (libro, capítulo, tipo de página, columnas).
4. OCR localizado por región (no OCR plano global).
5. Extracción de versículos guiada por zonas de `cuerpo_biblico`.

## Requisitos

- Python 3.10+
- Tesseract OCR instalado en sistema (con idioma `spa`)
- Dependencias Python:

```bash
pip install -r requirements.txt
```

## Ejecución

Coloca las imágenes dentro de `AI156_images/`:

- `AI156_0018.jpg`
- `AI156_0020.jpg`
- `AI156_0074.jpg`
- `AI156_0257.jpg`

Ejecuta:

```bash
python vision_first_extractor.py \
  --input-dir AI156_images \
  --output-dir outputs/vision_first
```

## Entregables generados

Dentro de `outputs/vision_first/`:

- `annotated/*.annotated.jpg`: imagen con cajas y etiquetas por zona.
- `crops/*.jpg`: recortes por zona detectada.
- `pages_json/*.page.json`: JSON estructurado por página.
- `verses_json/*.verses.json`: JSON estructurado por versículo.

## Notas

- Este prototipo está orientado a validación del enfoque visual.
- OCR se usa como etapa secundaria y localizada por zonas.
- No escribe en SQLite.
