# lumen-extractor

Extractor experimental (v0) para validar extracción de texto bíblico desde 4 imágenes escaneadas de la Biblia Torres Amat.

## Alcance v0

- Procesa únicamente estas 4 imágenes (desde `AI156_images`):
  - `AI156_0018.jpg`
  - `AI156_0020.jpg`
  - `AI156_0074.jpg`
  - `AI156_0257.jpg`
- Detecta layout en 2 columnas (heurística por valle vertical).
- Ejecuta OCR por columna (izquierda → derecha).
- Intenta detectar metadatos (`LIBRO DEL ...`, `CAPITULO ...`).
- Extrae versículos por patrón `^\d+\.`.
- Intenta excluir notas al pie (heurística de bloque inferior).
- Genera salidas JSON y texto por columna.

## Requisitos

- Python 3.10+
- Tesseract OCR instalado en el sistema (con idioma español `spa` disponible)
- Dependencias Python:

```bash
pip install -r requirements.txt
```

## Uso

```bash
python extractor_v0.py --images-dir AI156_images --output-dir output_v0
```

## Salidas

En `output_v0/` se generan:

- `image_report.json`: reporte por imagen (libro/capítulo/columnas/observaciones).
- `verses.json`: versículos estructurados preliminares.
- `anomalies.json`: advertencias y anomalías detectadas.
- `column_text/*.txt`: texto OCR preliminar por columna.

## Notas

Este proyecto es un prototipo de validación y no implementa aún el pipeline final (SQLite, cobertura completa del tomo o optimización avanzada).


## Iteración de mejoras (v0.2)

- OCR tokenizado con `image_to_data` para usar posiciones y reforzar separación de columnas.
- Corte de columnas por histograma de centros de tokens (reduce mezcla izquierda/derecha).
- Exclusión de notas al pie a nivel de token (zona inferior + tamaño relativo de fuente + marcadores).
- Segmentación de versículos más estricta por `^\d+\.` con controles de ruido para evitar absorción de notas.
- Detección de libro robusta con normalización de acentos para cabeceras como `LIBRO DEL GÉNESIS` y `LIBRO DEL ÉXODO`.

- Nueva etapa previa: segmentación vertical de página (cabecera / cuerpo bíblico / notas), aplicando OCR solo al cuerpo para extracción de versículos.
