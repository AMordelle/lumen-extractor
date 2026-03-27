# lumen-extractor

Extractor de texto para páginas bíblicas escaneadas con flujo de lectura humano (no OCR).

## Versión final recomendada

Script: `extractor_v6_ai_final_human_reader.py`

Características clave:
- Arquitectura de 4 fases obligatorias:
  1. Comprensión semántica de página.
  2. Transcripción literal por orden de lectura humano.
  3. Validación de cobertura visible (esperado vs extraído).
  4. Revisión mínima dirigida (solo dudosos/corruptos/páginas problemáticas).
- Uso de OpenAI Responses API (`gpt-4.1`, `temperature=0`, `detail=high`).
- Parseo JSON estricto y guardado de respuestas crudas por fase.
- Reporte manual con issues por versículo y por página.

## Ejecución

```bash
python extractor_v6_ai_final_human_reader.py \
  --input-dir AI156_images \
  --images AI156_0018.jpg AI156_0020.jpg AI156_0074.jpg AI156_0257.jpg
```

> Esta versión restringe intencionalmente la validación a esas 4 páginas objetivo.
