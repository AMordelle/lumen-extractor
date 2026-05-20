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

## Salidas

Se generan archivos en:

- `output/raw_responses/`: respuesta cruda completa de la API.
- `output/pages_json/`: JSON validado localmente.
- `output/review/`: reporte para revisión automática cuando hay dudas/warnings.

## Limitaciones actuales

- No hace procesamiento masivo.
- No usa base de datos ni interfaz gráfica.
- No exporta PDF.
- No une capítulos/páginas.
- No hace corrección bíblica avanzada ni comparación con otras Biblias.
- No devuelve bounding boxes ni coordenadas visuales.
