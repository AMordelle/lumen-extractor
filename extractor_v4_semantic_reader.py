#!/usr/bin/env python3
"""
Extractor v4 (semantic reader) for Torres Amat Bible scans.

Key design: holistic semantic page reading with the OpenAI Responses API.
No coordinate-based cropping, no layout geometry detection.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI


SYSTEM_PROMPT = (
    "Eres un experto paleógrafo y transcriptor bíblico de alta precisión. "
    "Tu trabajo es leer páginas escaneadas de la Biblia Torres Amat y extraer "
    "solamente los versículos bíblicos con transcripción estrictamente literal."
)

USER_PROMPT_TEMPLATE = """
Analiza esta imagen de página completa de forma semántica y holística, como un lector humano.

Objetivo:
- Detecta dónde empieza y termina el texto bíblico real.
- Ignora elementos no bíblicos.
- Extrae versículos en orden de lectura humano: columna izquierda (arriba→abajo), luego columna derecha (arriba→abajo).

Ignorar si aparecen:
- números de página
- ornamentos decorativos
- bordes e ilustraciones
- títulos de libro (por ejemplo: "LIBRO DEL GÉNESIS")
- encabezados de capítulo sin texto de versículo
- notas al pie o comentarios
- notas editoriales
- referencias cruzadas
- decoración marginal

Regla de detección de versículo:
- Cada versículo empieza en su número (ejemplo: "11.", "12.", "13.").
- Conserva el número.

Reglas de transcripción (OBLIGATORIAS):
- Literal y fiel al impreso.
- NO modernizar ortografía.
- NO normalizar puntuación.
- NO corregir gramática.
- NO quitar tildes.
- NO expandir abreviaturas.
- Mantener exactamente grafías como: "á", "ó", "dió", "vió", "á la mujer".

Si hay duda razonable sobre el número de versículo, inclúyelo igualmente y marca "dudoso": true.

Devuelve EXCLUSIVAMENTE un JSON válido con este esquema:
{
  "pagina": "{page_id}",
  "versiculos": [
    {
      "versiculo": 11,
      "texto": "Replicóle: ¿Pues quién te ha hecho advertir...",
      "dudoso": false
    }
  ]
}

Condiciones del JSON:
- "versiculo" debe ser entero.
- "texto" debe preservar literalidad.
- Mantener orden de lectura.
- Incluye "dudoso" (true/false) en cada versículo.
- Sin texto fuera del JSON.
""".strip()


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    def add(self, usage: dict[str, Any]) -> None:
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)
        self.total_tokens += int(usage.get("total_tokens", 0) or 0)

        output_details = usage.get("output_tokens_details") or {}
        self.reasoning_tokens += int(output_details.get("reasoning_tokens", 0) or 0)


def encode_image_data_url(image_path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime:
        mime = "image/jpeg"
    data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{data}"


def extract_json_object(raw_text: str) -> dict[str, Any]:
    raw_text = raw_text.strip()
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        # Defensive fallback if model wraps JSON with extra text.
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(raw_text[start : end + 1])


def normalize_output(page_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    verses = payload.get("versiculos", [])
    normalized: list[dict[str, Any]] = []

    for item in verses:
        try:
            verse_num = int(item.get("versiculo"))
        except (TypeError, ValueError):
            # If unusable, skip malformed entry.
            continue

        text = str(item.get("texto", ""))
        doubtful = bool(item.get("dudoso", False))
        normalized.append({"versiculo": verse_num, "texto": text, "dudoso": doubtful})

    return {"pagina": page_id, "versiculos": normalized}


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    price_input_per_1m: float,
    price_output_per_1m: float,
) -> float:
    return (input_tokens / 1_000_000) * price_input_per_1m + (
        output_tokens / 1_000_000
    ) * price_output_per_1m


def process_page(client: OpenAI, image_path: Path, output_dir: Path, model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    page_id = image_path.stem
    prompt = USER_PROMPT_TEMPLATE.format(page_id=page_id)
    data_url = encode_image_data_url(image_path)

    response = client.responses.create(
        model=model,
        temperature=0,
        reasoning={"effort": "low"},
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": data_url},
                ],
            },
        ],
    )

    raw = response.output_text
    parsed = extract_json_object(raw)
    normalized = normalize_output(page_id, parsed)

    out_path = output_dir / f"{page_id}.verses.json"
    out_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")

    usage = getattr(response, "usage", None)
    usage_dict: dict[str, Any] = {}
    if usage is not None:
        if hasattr(usage, "model_dump"):
            usage_dict = usage.model_dump()
        elif isinstance(usage, dict):
            usage_dict = usage

    return normalized, usage_dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Semantic verse extractor for Torres Amat Bible scans (v4)."
    )
    parser.add_argument("--input-dir", default="AI156_images", help="Folder containing page images.")
    parser.add_argument("--output-dir", default="output/v4_semantic", help="Folder for per-page verse JSON files.")
    parser.add_argument("--model", default="gpt-4.1", help="OpenAI model name.")
    parser.add_argument(
        "--price-input-per-1m",
        type=float,
        default=float(os.getenv("OPENAI_PRICE_INPUT_PER_1M", "2.0")),
        help="USD price per 1M input tokens for cost estimate.",
    )
    parser.add_argument(
        "--price-output-per-1m",
        type=float,
        default=float(os.getenv("OPENAI_PRICE_OUTPUT_PER_1M", "8.0")),
        help="USD price per 1M output tokens for cost estimate.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    image_files = sorted(
        p
        for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
    )

    if not image_files:
        raise RuntimeError(f"No supported images found in {input_dir}")

    client = OpenAI()
    totals = UsageTotals()
    page_reports: list[dict[str, Any]] = []

    for image_path in image_files:
        page_data, usage = process_page(client, image_path, output_dir, args.model)
        totals.add(usage)
        page_reports.append(
            {
                "pagina": page_data["pagina"],
                "archivo_salida": f"{page_data['pagina']}.verses.json",
                "usage": usage,
                "versiculos_extraidos": len(page_data.get("versiculos", [])),
            }
        )
        print(f"Processed {image_path.name} -> {page_data['pagina']}.verses.json")

    estimated_cost = estimate_cost_usd(
        totals.input_tokens,
        totals.output_tokens,
        args.price_input_per_1m,
        args.price_output_per_1m,
    )

    summary = {
        "modelo": args.model,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "paginas_procesadas": len(image_files),
        "totales_tokens": {
            "input_tokens": totals.input_tokens,
            "output_tokens": totals.output_tokens,
            "reasoning_tokens": totals.reasoning_tokens,
            "total_tokens": totals.total_tokens,
        },
        "estimacion_coste_usd": round(estimated_cost, 6),
        "precios_usd_por_1m": {
            "input": args.price_input_per_1m,
            "output": args.price_output_per_1m,
        },
        "detalle_paginas": page_reports,
    }

    summary_path = output_dir / "batch_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Batch summary written to {summary_path}")


if __name__ == "__main__":
    main()
