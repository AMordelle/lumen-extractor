# lumen-extractor

Extractor **vision-first v2** para transcripción literal de versículos en páginas escaneadas de la Biblia Torres Amat.

## Qué cambia en v2

- El flujo ahora prioriza **layout visual** y **orden de lectura por columnas**.
- La transcripción es **literal**: no corrige ortografía, no moderniza, no completa texto.
- Solo extrae el **texto bíblico principal** (ignora notas al pie y ruido editorial).
- Salida principal: JSON por imagen con objetos por versículo en formato estricto.

## Requisitos

- Python 3.10+
- Tesseract OCR instalado con idioma `spa`
- Dependencias:

```bash
pip install -r requirements.txt
```

## Ejecución

Imágenes esperadas en `AI156_images/`:

- `AI156_0018.jpg`
- `AI156_0020.jpg`
- `AI156_0074.jpg`
- `AI156_0257.jpg`

Ejecutar:

```bash
python vision_first_extractor.py \
  --input-dir AI156_images \
  --output-dir outputs/vision_first_v2
```

## Artefactos generados

- `outputs/vision_first_v2/annotated/*.annotated.jpg`: cajas visuales por zona.
- `outputs/vision_first_v2/crops/*.jpg`: recortes por zona.
- `outputs/vision_first_v2/pages_json/*.page.json`: metadatos de layout por página.
- `outputs/vision_first_v2/verses_json/*.verses.json`: lista JSON de versículos:

```json
[
  {
    "imagen_origen": "AI156_0018.jpg",
    "libro": "Génesis",
    "capitulo": 1,
    "versiculo": 1,
    "texto": "En el principio ..."
  }
]
```

## Orden de lectura aplicado

1. Columna izquierda (arriba -> abajo)
2. Columna derecha (arriba -> abajo)

El sistema concatena líneas continuadas del mismo versículo y mantiene el texto OCR tal como se reconoce.
