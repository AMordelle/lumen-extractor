#!/usr/bin/env python3
"""Extractor v3: layout-first literal transcription for Biblia Torres Amat scans.

Pipeline:
1) Layout detection on full page.
2) Crop biblical columns using detected bboxes.
3) Literal transcription per cropped column.
4) Merge verses in reading order.
5) Persist structured outputs and cost logs.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw


MODEL = "gpt-4.1"
TEMPERATURE = 0
TOP_P = 1
LOGGER = logging.getLogger("extractor_v3")

TEST_IMAGES = [
    "AI156_0018.jpg",
    "AI156_0020.jpg",
    "AI156_0074.jpg",
    "AI156_0257.jpg",
]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class CostBook:
    # conservative placeholders; adjust if your account pricing differs
    input_per_million: float = 5.0
    output_per_million: float = 15.0

    def estimate_usd(self, usage: Usage) -> float:
        in_cost = (usage.input_tokens / 1_000_000) * self.input_per_million
        out_cost = (usage.output_tokens / 1_000_000) * self.output_per_million
        return round(in_cost + out_cost, 6)


def encode_image_to_data_uri(image_path: Path) -> str:
    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def ensure_dirs(paths: List[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def _extract_usage(resp_json: Dict[str, Any]) -> Usage:
    usage = resp_json.get("usage") or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    if output_tokens == 0:
        output_tokens = usage.get("output_tokens_details", {}).get("text_tokens", 0)
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


def _extract_text_output(resp_json: Dict[str, Any]) -> str:
    output = resp_json.get("output", [])
    chunks: List[str] = []
    for item in output:
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    if chunks:
        return "\n".join(chunks).strip()
    if resp_json.get("output_text"):
        return str(resp_json["output_text"]).strip()
    raise ValueError("No textual output found in API response")


def _clean_json_text(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _json_request(
    api_key: str,
    payload: Dict[str, Any],
    raw_response_path: Path,
    retry_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Usage]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers=headers,
        json=payload,
        timeout=180,
    )
    response.raise_for_status()
    data = response.json()
    raw_response_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    usage = _extract_usage(data)
    text = _clean_json_text(_extract_text_output(data))
    try:
        return json.loads(text), usage
    except json.JSONDecodeError:
        if not retry_payload:
            raise

    retry_resp = requests.post(
        "https://api.openai.com/v1/responses",
        headers=headers,
        json=retry_payload,
        timeout=180,
    )
    retry_resp.raise_for_status()
    retry_data = retry_resp.json()
    retry_path = raw_response_path.with_name(raw_response_path.stem + ".retry.json")
    retry_path.write_text(json.dumps(retry_data, ensure_ascii=False, indent=2), encoding="utf-8")

    retry_usage = _extract_usage(retry_data)
    retry_text = _clean_json_text(_extract_text_output(retry_data))
    parsed = json.loads(retry_text)
    return parsed, Usage(
        input_tokens=usage.input_tokens + retry_usage.input_tokens,
        output_tokens=usage.output_tokens + retry_usage.output_tokens,
    )


def validate_layout_json(layout: Dict[str, Any]) -> Dict[str, Any]:
    required = [
        "imagen_origen",
        "libro",
        "capitulo",
        "tipo_pagina",
        "pagina_mixta",
        "columnas_biblicas",
        "tiene_notas_al_pie",
        "tiene_ornamento_central",
        "bbox_columna_izquierda",
        "bbox_columna_derecha",
        "bbox_notas",
        "bbox_titulo_libro",
        "bbox_titulo_capitulo",
    ]
    for key in required:
        if key not in layout:
            raise ValueError(f"Missing layout key: {key}")

    for bbox_key in [
        "bbox_columna_izquierda",
        "bbox_columna_derecha",
        "bbox_notas",
        "bbox_titulo_libro",
        "bbox_titulo_capitulo",
    ]:
        bbox = layout.get(bbox_key)
        if bbox is None:
            continue
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"Invalid bbox format for {bbox_key}: {bbox}")

    return layout


def normalize_verse_number(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        # Accept clean numeric strings quickly
        if candidate.isdigit():
            return int(candidate)
        # Accept common suffix punctuation / marks like "12.", "12:", "12*", "12,"
        match = re.match(r"^(\d+)", candidate)
        if match:
            return int(match.group(1))
    return None


def validate_column_json(items: Any, column_name: str, image_name: str) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        LOGGER.error(
            "Column transcription is not a list | image=%s column=%s type=%s",
            image_name,
            column_name,
            type(items).__name__,
        )
        return []
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            LOGGER.warning(
                "Skipping non-object transcription item | image=%s column=%s item=%s",
                image_name,
                column_name,
                repr(item),
            )
            continue
        if "versiculo" not in item or "texto" not in item:
            LOGGER.warning(
                "Skipping item missing versiculo/texto | image=%s column=%s item=%s",
                image_name,
                column_name,
                repr(item),
            )
            continue

        verse_number = normalize_verse_number(item.get("versiculo"))
        if verse_number is None:
            LOGGER.warning(
                "Invalid versiculo value skipped | image=%s column=%s versiculo=%s texto=%s",
                image_name,
                column_name,
                repr(item.get("versiculo")),
                repr(item.get("texto", "")),
            )
            continue

        normalized.append(
            {
                "columna": item.get("columna", column_name),
                "versiculo": verse_number,
                "texto": str(item["texto"]).strip(),
            }
        )

    if not normalized:
        LOGGER.error(
            "No valid verses extracted for column | image=%s column=%s",
            image_name,
            column_name,
        )
    return normalized


def bbox_to_crop_box(bbox: List[int], width: int, height: int) -> Tuple[int, int, int, int]:
    x, y, w, h = [int(v) for v in bbox]
    left = max(0, x)
    top = max(0, y)
    right = min(width, x + w)
    bottom = min(height, y + h)
    if right <= left or bottom <= top:
        raise ValueError(f"Invalid crop box derived from bbox: {bbox}")
    return left, top, right, bottom


def step1_layout_detect(
    api_key: str,
    image_path: Path,
    raw_dir: Path,
) -> Tuple[Dict[str, Any], Usage]:
    image_data_uri = encode_image_to_data_uri(image_path)

    instruction = (
        "Analiza SOLAMENTE la estructura visual de esta página escaneada de Biblia Torres Amat. "
        "No transcribas el texto completo. "
        "Identifica regiones: título de libro, título de capítulo, sumario de capítulo, "
        "zona principal de lectura bíblica, columna bíblica izquierda, columna bíblica derecha (si existe), "
        "notas al pie, ornamento central decorativo, tipo de página y si es mixta. "
        "Prioriza detectar el área principal de lectura bíblica. "
        "Devuelve JSON estricto con este esquema y sin texto adicional: "
        "{"
        "\"imagen_origen\":str,"
        "\"libro\":str,"
        "\"capitulo\":int|null,"
        "\"tipo_pagina\":str,"
        "\"pagina_mixta\":bool,"
        "\"columnas_biblicas\":int,"
        "\"tiene_notas_al_pie\":bool,"
        "\"tiene_ornamento_central\":bool,"
        "\"bbox_columna_izquierda\":[x,y,w,h]|null,"
        "\"bbox_columna_derecha\":[x,y,w,h]|null,"
        "\"bbox_notas\":[x,y,w,h]|null,"
        "\"bbox_titulo_libro\":[x,y,w,h]|null,"
        "\"bbox_titulo_capitulo\":[x,y,w,h]|null"
        "}"
    )

    payload = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": instruction},
                    {
                        "type": "input_image",
                        "image_url": image_data_uri,
                        "detail": "high",
                    },
                ],
            }
        ],
    }

    retry_instruction = instruction + " Responde SOLO JSON válido, sin markdown, sin comentarios, sin prefijos."
    retry_payload = {
        **payload,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": retry_instruction},
                    {
                        "type": "input_image",
                        "image_url": image_data_uri,
                        "detail": "high",
                    },
                ],
            }
        ],
    }

    raw_path = raw_dir / f"{image_path.stem}.layout.json"
    parsed, usage = _json_request(api_key, payload, raw_path, retry_payload=retry_payload)
    return validate_layout_json(parsed), usage


def step2_crop_columns(
    image_path: Path,
    layout: Dict[str, Any],
    crops_dir: Path,
    annotated_dir: Path,
) -> Dict[str, Optional[Path]]:
    with Image.open(image_path) as img:
        width, height = img.size
        draw = ImageDraw.Draw(img)

        output: Dict[str, Optional[Path]] = {"left": None, "right": None}

        left_bbox = layout.get("bbox_columna_izquierda")
        if left_bbox:
            left_crop_box = bbox_to_crop_box(left_bbox, width, height)
            left_crop = img.crop(left_crop_box)
            left_path = crops_dir / f"{image_path.stem}.left.jpg"
            left_crop.save(left_path, "JPEG", quality=95)
            output["left"] = left_path
            draw.rectangle(left_crop_box, outline="red", width=4)

        right_bbox = layout.get("bbox_columna_derecha")
        if right_bbox:
            right_crop_box = bbox_to_crop_box(right_bbox, width, height)
            right_crop = img.crop(right_crop_box)
            right_path = crops_dir / f"{image_path.stem}.right.jpg"
            right_crop.save(right_path, "JPEG", quality=95)
            output["right"] = right_path
            draw.rectangle(right_crop_box, outline="blue", width=4)

        extras = [
            (layout.get("bbox_notas"), "yellow"),
            (layout.get("bbox_titulo_libro"), "green"),
            (layout.get("bbox_titulo_capitulo"), "purple"),
        ]
        for bbox, color in extras:
            if bbox:
                draw.rectangle(bbox_to_crop_box(bbox, width, height), outline=color, width=3)

        annotated_path = annotated_dir / f"{image_path.stem}.annotated.jpg"
        img.save(annotated_path, "JPEG", quality=95)

    return output


def step3_transcribe_column(
    api_key: str,
    crop_path: Path,
    column_name: str,
    raw_dir: Path,
) -> Tuple[List[Dict[str, Any]], Usage]:
    image_data_uri = encode_image_to_data_uri(crop_path)
    prompt = (
        "Eres un copista literal de texto bíblico antiguo. "
        "Transcribe exactamente lo visible en esta columna bíblica. "
        "Reglas obligatorias: "
        "no corregir gramática; "
        "no modernizar ortografía; "
        "no completar palabras; "
        "no reemplazar palabras inusuales; "
        "no reescribir ni mejorar estilo; "
        "preservar acentos y puntuación como aparecen; "
        "si hay duda, elegir lo más cercano a lo visible; "
        "no incluir notas al pie; "
        "no incluir sumarios editoriales; "
        "no incluir texto decorativo. "
        f"Devuelve SOLO JSON válido como lista de objetos con claves exactas: columna, versiculo, texto. "
        f"Usa columna='{column_name}'."
    )

    payload = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": image_data_uri,
                        "detail": "high",
                    },
                ],
            }
        ],
    }

    retry_prompt = prompt + " Responde ÚNICAMENTE JSON parseable; sin markdown; sin explicación."
    retry_payload = {
        **payload,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": retry_prompt},
                    {
                        "type": "input_image",
                        "image_url": image_data_uri,
                        "detail": "high",
                    },
                ],
            }
        ],
    }

    raw_path = raw_dir / f"{crop_path.stem}.transcription.json"
    parsed, usage = _json_request(api_key, payload, raw_path, retry_payload=retry_payload)
    normalized = validate_column_json(parsed, column_name, crop_path.name)
    return normalized, usage


def setup_logging(logs_dir: Path) -> None:
    ensure_dirs([logs_dir])
    log_path = logs_dir / "extractor_v3_debug.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def step4_merge_verses(
    image_name: str,
    layout: Dict[str, Any],
    left_verses: List[Dict[str, Any]],
    right_verses: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for item in left_verses + right_verses:
        merged.append(
            {
                "imagen_origen": image_name,
                "libro": layout.get("libro"),
                "capitulo": layout.get("capitulo"),
                "versiculo": item["versiculo"],
                "texto": item["texto"],
            }
        )
    return merged


def process_page(
    image_path: Path,
    api_key: str,
    output_root: Path,
    cost_book: CostBook,
) -> Dict[str, Any]:
    crops_dir = output_root / "crops_v3"
    annotated_dir = output_root / "annotated_v3"
    verses_dir = output_root / "verses_json_v3"
    pages_dir = output_root / "pages_json_v3"
    logs_dir = output_root / "logs_v3"
    raw_dir = logs_dir / "raw_api"

    ensure_dirs([crops_dir, annotated_dir, verses_dir, pages_dir, logs_dir, raw_dir])

    usage_total = Usage()

    layout, usage = step1_layout_detect(api_key, image_path, raw_dir)
    usage_total.input_tokens += usage.input_tokens
    usage_total.output_tokens += usage.output_tokens

    crops = step2_crop_columns(image_path, layout, crops_dir, annotated_dir)

    left_verses: List[Dict[str, Any]] = []
    right_verses: List[Dict[str, Any]] = []

    if crops.get("left"):
        left_verses, usage = step3_transcribe_column(api_key, crops["left"], "izquierda", raw_dir)
        usage_total.input_tokens += usage.input_tokens
        usage_total.output_tokens += usage.output_tokens

    if crops.get("right"):
        right_verses, usage = step3_transcribe_column(api_key, crops["right"], "derecha", raw_dir)
        usage_total.input_tokens += usage.input_tokens
        usage_total.output_tokens += usage.output_tokens

    merged = step4_merge_verses(image_path.name, layout, left_verses, right_verses)

    page_json_path = pages_dir / f"{image_path.stem}.page.json"
    page_json_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")

    verses_json_path = verses_dir / f"{image_path.stem}.verses.json"
    verses_json_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "imagen_origen": image_path.name,
        "input_tokens": usage_total.input_tokens,
        "output_tokens": usage_total.output_tokens,
        "total_tokens": usage_total.total_tokens,
        "estimated_cost_usd": cost_book.estimate_usd(usage_total),
        "verses_count": len(merged),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Layout-first literal extractor v3")
    parser.add_argument("--input-dir", default="AI156_images", help="Input image folder")
    parser.add_argument("--output-dir", default="output", help="Base output folder")
    parser.add_argument(
        "--images",
        nargs="*",
        default=TEST_IMAGES,
        help="Subset of image filenames to process",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is required")

    input_dir = Path(args.input_dir)
    output_root = Path(args.output_dir)
    setup_logging(output_root / "logs_v3")
    cost_book = CostBook()

    batch: List[Dict[str, Any]] = []
    for image_name in args.images:
        image_path = input_dir / image_name
        if not image_path.exists():
            raise FileNotFoundError(f"Missing image: {image_path}")
        summary = process_page(image_path, api_key, output_root, cost_book)
        batch.append(summary)

    logs_dir = output_root / "logs_v3"
    ensure_dirs([logs_dir])

    total_input = sum(item["input_tokens"] for item in batch)
    total_output = sum(item["output_tokens"] for item in batch)
    total_tokens = sum(item["total_tokens"] for item in batch)
    total_cost = round(sum(item["estimated_cost_usd"] for item in batch), 6)

    batch_summary = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "pages": batch,
        "batch_totals": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_tokens,
            "estimated_cost_usd": total_cost,
        },
    }

    (logs_dir / "batch_summary.json").write_text(
        json.dumps(batch_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
