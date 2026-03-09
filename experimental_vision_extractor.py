#!/usr/bin/env python3
"""Experimental vision-first Bible extractor using OpenAI API."""

from __future__ import annotations

import argparse
import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any



DEFAULT_IMAGES = [
    "AI156_0018.jpg",
    "AI156_0020.jpg",
    "AI156_0074.jpg",
    "AI156_0257.jpg",
]

PROMPT_BASE = """
Eres un lector documental experto en biblias antiguas en español. Debes interpretar la página visualmente (no como OCR plano) y devolver SOLO JSON válido.

Objetivo: extraer estructura de página y texto bíblico.

Reglas críticas:
1) No descartar páginas de inicio de libro (ej. "LIBRO DEL GÉNESIS" o "LIBRO DEL ÉXODO"). Pueden ser páginas mixtas con texto bíblico.
2) Excluir notas al pie y resúmenes/editoriales del texto bíblico.
3) Ignorar ornamentos centrales o elementos decorativos no textuales.
4) Respetar orden de lectura bíblica: primero columna izquierda, luego derecha.
5) Si falta certeza exacta, usar el mejor juicio visual e incluir detalles en observaciones.

Devuelve este JSON exacto (sin markdown):
{
  "page": {
    "imagen_origen": "<nombre_archivo>",
    "libro": "<string>",
    "capitulo": <int|null>,
    "tipo_pagina": "<string>",
    "pagina_mixta": <bool>,
    "columnas_biblicas": <int|null>,
    "tiene_notas_al_pie": <bool>,
    "tiene_ornamento_central": <bool>,
    "versiculo_inicio_visible": <int|null>,
    "versiculo_fin_visible": <int|null>,
    "observaciones": ["<string>"]
  },
  "verses": [
    {
      "imagen_origen": "<nombre_archivo>",
      "libro": "<string>",
      "capitulo": <int|null>,
      "versiculo": <int>,
      "texto": "<texto_biblico_sin_notas_ni_editorial>"
    }
  ]
}
""".strip()

STRICT_JSON_SUFFIX = "\n\nRESPONDE EXCLUSIVAMENTE JSON VÁLIDO, SIN TEXTO ADICIONAL."


@dataclass
class ExtractorConfig:
    input_dir: Path = Path("AI156_images")
    output_dir: Path = Path("output")
    image_names: list[str] = field(default_factory=lambda: list(DEFAULT_IMAGES))
    resize_max_width: int = 1600
    resize_max_height: int = 2300
    model: str = "gpt-4.1"
    max_output_tokens: int = 8000
    retry_attempts: int = 1
    usd_per_1m_input_tokens: float = 2.00
    usd_per_1m_output_tokens: float = 8.00


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Experimental OpenAI vision-first extractor")
    parser.add_argument("--input-dir", default="AI156_images")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument(
        "--images",
        nargs="*",
        default=DEFAULT_IMAGES,
        help="Image names to process. Defaults to the 4 validation pages.",
    )
    parser.add_argument("--resize-max-width", type=int, default=1600)
    parser.add_argument("--resize-max-height", type=int, default=2300)
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--max-output-tokens", type=int, default=8000)
    parser.add_argument("--retry-attempts", type=int, default=1, help="Retries after initial malformed JSON.")
    parser.add_argument("--usd-per-1m-input-tokens", type=float, default=2.0)
    parser.add_argument("--usd-per-1m-output-tokens", type=float, default=8.0)
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> ExtractorConfig:
    return ExtractorConfig(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        image_names=list(args.images),
        resize_max_width=args.resize_max_width,
        resize_max_height=args.resize_max_height,
        model=args.model,
        max_output_tokens=args.max_output_tokens,
        retry_attempts=args.retry_attempts,
        usd_per_1m_input_tokens=args.usd_per_1m_input_tokens,
        usd_per_1m_output_tokens=args.usd_per_1m_output_tokens,
    )


def ensure_dirs(base_output_dir: Path) -> dict[str, Path]:
    dirs = {
        "resized": base_output_dir / "resized",
        "pages_json": base_output_dir / "pages_json",
        "verses_json": base_output_dir / "verses_json",
        "logs": base_output_dir / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def resize_for_api(src_path: Path, dst_path: Path, max_width: int, max_height: int) -> tuple[int, int]:
    from PIL import Image

    with Image.open(src_path) as img:
        img = img.convert("RGB")
        original_w, original_h = img.size
        scale = min(max_width / original_w, max_height / original_h, 1.0)
        new_size = (int(original_w * scale), int(original_h * scale))
        resized = img.resize(new_size, Image.Resampling.LANCZOS)
        resized.save(dst_path, format="JPEG", quality=85, optimize=True)
    return new_size


def image_to_data_url(path: Path) -> str:
    content = path.read_bytes()
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def parse_json_response(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def validate_payload(payload: dict[str, Any]) -> None:
    if "page" not in payload or "verses" not in payload:
        raise ValueError("Missing required top-level keys: page, verses")
    if not isinstance(payload["page"], dict) or not isinstance(payload["verses"], list):
        raise ValueError("Invalid JSON structure for page/verses")


def estimate_cost(usage: dict[str, int], cfg: ExtractorConfig) -> float:
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    return (in_tok / 1_000_000 * cfg.usd_per_1m_input_tokens) + (
        out_tok / 1_000_000 * cfg.usd_per_1m_output_tokens
    )


def extract_page(client: Any, image_name: str, resized_path: Path, cfg: ExtractorConfig, logs_dir: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    prompt = PROMPT_BASE.replace("<nombre_archivo>", image_name)
    last_error = ""

    for attempt in range(cfg.retry_attempts + 1):
        strict = attempt > 0
        user_text = prompt + (STRICT_JSON_SUFFIX if strict else "")

        response = client.responses.create(
            model=cfg.model,
            max_output_tokens=cfg.max_output_tokens,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_text},
                        {"type": "input_image", "image_url": image_to_data_url(resized_path)},
                    ],
                }
            ],
        )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        raw_path = logs_dir / f"{image_name}.{timestamp}.attempt{attempt+1}.raw_response.json"
        raw_path.write_text(response.model_dump_json(indent=2), encoding="utf-8")

        output_text = response.output_text or ""
        text_path = logs_dir / f"{image_name}.{timestamp}.attempt{attempt+1}.response_text.txt"
        text_path.write_text(output_text, encoding="utf-8")

        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0) if getattr(response, "usage", None) else 0,
            "output_tokens": getattr(response.usage, "output_tokens", 0) if getattr(response, "usage", None) else 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) if getattr(response, "usage", None) else 0,
        }

        try:
            payload = parse_json_response(output_text)
            validate_payload(payload)
            return payload, usage, "ok"
        except Exception as exc:  # noqa: BLE001
            last_error = f"Attempt {attempt+1} JSON parse failed: {exc}"

    raise ValueError(last_error or "Unknown extraction failure")


def run(cfg: ExtractorConfig) -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env file")

    client = OpenAI()
    dirs = ensure_dirs(cfg.output_dir)
    batch_summary: list[dict[str, Any]] = []

    for image_name in cfg.image_names:
        src = cfg.input_dir / image_name
        resized = dirs["resized"] / image_name
        row: dict[str, Any] = {
            "image_name": image_name,
            "status": "error",
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "error": None,
        }

        if not src.exists():
            row["error"] = f"Image not found: {src}"
            batch_summary.append(row)
            continue

        try:
            resized_w, resized_h = resize_for_api(
                src,
                resized,
                max_width=cfg.resize_max_width,
                max_height=cfg.resize_max_height,
            )

            payload, usage, status = extract_page(client, image_name, resized, cfg, dirs["logs"])
            payload["page"]["imagen_origen"] = image_name
            for verse in payload["verses"]:
                verse["imagen_origen"] = image_name

            page_json_path = dirs["pages_json"] / f"{Path(image_name).stem}.page.json"
            verses_json_path = dirs["verses_json"] / f"{Path(image_name).stem}.verses.json"
            page_json_path.write_text(json.dumps(payload["page"], ensure_ascii=False, indent=2), encoding="utf-8")
            verses_json_path.write_text(json.dumps(payload["verses"], ensure_ascii=False, indent=2), encoding="utf-8")

            row.update(
                {
                    "status": status,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "estimated_cost_usd": round(estimate_cost(usage, cfg), 6),
                    "resized_dimensions": [resized_w, resized_h],
                }
            )
        except Exception as exc:  # noqa: BLE001
            row["error"] = str(exc)

        batch_summary.append(row)

    total_cost = round(sum(item.get("estimated_cost_usd", 0.0) for item in batch_summary), 6)
    totals = {
        "processed_pages": len(batch_summary),
        "successful_pages": sum(1 for x in batch_summary if x["status"] == "ok"),
        "failed_pages": sum(1 for x in batch_summary if x["status"] != "ok"),
        "total_input_tokens": sum(x.get("input_tokens", 0) for x in batch_summary),
        "total_output_tokens": sum(x.get("output_tokens", 0) for x in batch_summary),
        "total_tokens": sum(x.get("total_tokens", 0) for x in batch_summary),
        "total_estimated_cost_usd": total_cost,
        "model": cfg.model,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    summary_path = dirs["logs"] / "batch_summary.json"
    summary_path.write_text(json.dumps({"pages": batch_summary, "totals": totals}, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote summary: {summary_path}")
    print(json.dumps(totals, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    args = parse_args()
    run(build_config(args))
