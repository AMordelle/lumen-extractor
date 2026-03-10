#!/usr/bin/env python3
"""Phase 2: verify cleaned verses against original page image using OpenAI API."""

from __future__ import annotations

import argparse
import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_IMAGES = [
    "AI156_0018.jpg",
    "AI156_0020.jpg",
    "AI156_0074.jpg",
    "AI156_0257.jpg",
]

ALLOWED_STATES = {"verificado", "corregido", "dudoso"}

VERIFY_PROMPT_BASE = """
Eres un verificador documental de una Biblia histórica en español.

TAREA: verificar una transcripción YA EXISTENTE contra una imagen de página.
NO rehagas una extracción desde cero. Debes comparar la lista de versículos entregada con la imagen.

Reglas obligatorias:
1) Conserva ortografía histórica y estilo original.
2) No modernices, no resumas, no reescribas libremente.
3) Corrige solo discrepancias evidentes confirmables en la imagen.
4) Si una corrección no es claramente verificable, marca estado_verificacion="dudoso".
5) Estados permitidos por versículo: verificado | corregido | dudoso.

Debes revisar especialmente:
- duplicaciones accidentales,
- palabras faltantes,
- errores obvios de lectura,
- versículos truncados,
- puntuación claramente errónea,
- fragmentos que no coinciden con la imagen.

Devuelve SOLO JSON válido con este formato exacto:
{
  "verses_verified": [
    {
      "imagen_origen": "<nombre_archivo>",
      "libro": "<string>",
      "capitulo": <int|null>,
      "versiculo": <int>,
      "texto": "<texto_verificado_o_corregido>",
      "estado_verificacion": "verificado|corregido|dudoso"
    }
  ],
  "verification_report": {
    "imagen": "<nombre_archivo>",
    "versiculos_totales": <int>,
    "versiculos_verificados": <int>,
    "versiculos_corregidos": <int>,
    "versiculos_dudosos": <int>,
    "lista_cambios_aplicados": [
      {
        "versiculo": <int>,
        "antes": "<texto_entrada>",
        "despues": "<texto_salida>",
        "motivo": "<breve_motivo>"
      }
    ]
  }
}
""".strip()

STRICT_JSON_SUFFIX = "\n\nRESPONDE EXCLUSIVAMENTE JSON VÁLIDO, SIN TEXTO ADICIONAL."


@dataclass
class VerificationConfig:
    images_dir: Path = Path("AI156_images")
    cleaned_dir: Path = Path("output/cleaned")
    output_dir: Path = Path("output/verified")
    image_names: list[str] = field(default_factory=lambda: list(DEFAULT_IMAGES))
    model: str = "gpt-4.1"
    max_output_tokens: int = 8000
    retry_attempts: int = 1
    usd_per_1m_input_tokens: float = 2.0
    usd_per_1m_output_tokens: float = 8.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2 verifier for cleaned verses against original image")
    parser.add_argument("--images-dir", default="AI156_images")
    parser.add_argument("--cleaned-dir", default="output/cleaned")
    parser.add_argument("--output-dir", default="output/verified")
    parser.add_argument("--images", nargs="*", default=DEFAULT_IMAGES)
    parser.add_argument("--model", default="gpt-4.1")
    parser.add_argument("--max-output-tokens", type=int, default=8000)
    parser.add_argument("--retry-attempts", type=int, default=1)
    parser.add_argument("--usd-per-1m-input-tokens", type=float, default=2.0)
    parser.add_argument("--usd-per-1m-output-tokens", type=float, default=8.0)
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> VerificationConfig:
    return VerificationConfig(
        images_dir=Path(args.images_dir),
        cleaned_dir=Path(args.cleaned_dir),
        output_dir=Path(args.output_dir),
        image_names=list(args.images),
        model=args.model,
        max_output_tokens=args.max_output_tokens,
        retry_attempts=args.retry_attempts,
        usd_per_1m_input_tokens=args.usd_per_1m_input_tokens,
        usd_per_1m_output_tokens=args.usd_per_1m_output_tokens,
    )


def ensure_dirs(base_dir: Path) -> dict[str, Path]:
    dirs = {
        "root": base_dir,
        "logs": base_dir / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def image_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
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


def validate_payload(payload: dict[str, Any], image_name: str, expected_total: int) -> None:
    if "verses_verified" not in payload or "verification_report" not in payload:
        raise ValueError("Missing required keys: verses_verified / verification_report")

    verses = payload["verses_verified"]
    report = payload["verification_report"]

    if not isinstance(verses, list):
        raise ValueError("verses_verified must be list")
    if not isinstance(report, dict):
        raise ValueError("verification_report must be object")

    for verse in verses:
        if not isinstance(verse, dict):
            raise ValueError("Invalid verse item")
        state = verse.get("estado_verificacion")
        if state not in ALLOWED_STATES:
            raise ValueError(f"Invalid estado_verificacion: {state}")
        if verse.get("imagen_origen") != image_name:
            raise ValueError("imagen_origen mismatch in verse item")

    if report.get("imagen") != image_name:
        raise ValueError("verification_report.imagen mismatch")
    if not isinstance(report.get("versiculos_totales"), int):
        raise ValueError("verification_report.versiculos_totales must be int")

    if report.get("versiculos_totales") != expected_total:
        raise ValueError("verification_report.versiculos_totales mismatch vs input verses")


def estimate_cost(usage: dict[str, int], cfg: VerificationConfig) -> float:
    in_tok = usage.get("input_tokens", 0)
    out_tok = usage.get("output_tokens", 0)
    return (in_tok / 1_000_000 * cfg.usd_per_1m_input_tokens) + (out_tok / 1_000_000 * cfg.usd_per_1m_output_tokens)


def verify_with_api(
    client: Any,
    image_name: str,
    image_path: Path,
    verses_clean: list[dict[str, Any]],
    cfg: VerificationConfig,
    logs_dir: Path,
) -> tuple[dict[str, Any], dict[str, int]]:
    verses_input_json = json.dumps(verses_clean, ensure_ascii=False, indent=2)
    user_text = (
        VERIFY_PROMPT_BASE.replace("<nombre_archivo>", image_name)
        + "\n\nVersículos de entrada para verificar (NO re-extraer):\n"
        + verses_input_json
    )

    last_error = ""
    for attempt in range(cfg.retry_attempts + 1):
        strict_text = user_text + (STRICT_JSON_SUFFIX if attempt > 0 else "")

        response = client.responses.create(
            model=cfg.model,
            max_output_tokens=cfg.max_output_tokens,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": strict_text},
                        {"type": "input_image", "image_url": image_to_data_url(image_path)},
                    ],
                }
            ],
        )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (logs_dir / f"{image_name}.{stamp}.attempt{attempt+1}.raw_response.json").write_text(
            response.model_dump_json(indent=2),
            encoding="utf-8",
        )
        output_text = response.output_text or ""
        (logs_dir / f"{image_name}.{stamp}.attempt{attempt+1}.response_text.txt").write_text(
            output_text,
            encoding="utf-8",
        )

        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0) if getattr(response, "usage", None) else 0,
            "output_tokens": getattr(response.usage, "output_tokens", 0) if getattr(response, "usage", None) else 0,
            "total_tokens": getattr(response.usage, "total_tokens", 0) if getattr(response, "usage", None) else 0,
        }

        try:
            payload = parse_json_response(output_text)
            validate_payload(payload, image_name=image_name, expected_total=len(verses_clean))
            return payload, usage
        except Exception as exc:  # noqa: BLE001
            last_error = f"Attempt {attempt+1} failed: {exc}"

    raise ValueError(last_error or "Verification failed")


def run(cfg: VerificationConfig) -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    client = OpenAI()
    dirs = ensure_dirs(cfg.output_dir)

    batch_rows: list[dict[str, Any]] = []

    for image_name in cfg.image_names:
        stem = Path(image_name).stem
        image_path = cfg.images_dir / image_name
        clean_path = cfg.cleaned_dir / f"{stem}.verses_clean.json"
        out_verified = cfg.output_dir / f"{stem}.verses_verified.json"
        out_report = cfg.output_dir / f"{stem}.verification_report.json"

        row: dict[str, Any] = {
            "imagen": image_name,
            "status": "error",
            "versiculos_totales": 0,
            "versiculos_verificados": 0,
            "versiculos_corregidos": 0,
            "versiculos_dudosos": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "error": None,
        }

        try:
            if not image_path.exists():
                raise FileNotFoundError(f"Image not found: {image_path}")
            if not clean_path.exists():
                raise FileNotFoundError(f"Clean verses not found: {clean_path}")

            verses_clean = json.loads(clean_path.read_text(encoding="utf-8"))
            if not isinstance(verses_clean, list):
                raise ValueError(f"Invalid verses file format: {clean_path}")

            payload, usage = verify_with_api(
                client=client,
                image_name=image_name,
                image_path=image_path,
                verses_clean=verses_clean,
                cfg=cfg,
                logs_dir=dirs["logs"],
            )

            verses_verified = payload["verses_verified"]
            report = payload["verification_report"]

            out_verified.write_text(json.dumps(verses_verified, ensure_ascii=False, indent=2), encoding="utf-8")
            out_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

            row.update(
                {
                    "status": "ok",
                    "versiculos_totales": report.get("versiculos_totales", 0),
                    "versiculos_verificados": report.get("versiculos_verificados", 0),
                    "versiculos_corregidos": report.get("versiculos_corregidos", 0),
                    "versiculos_dudosos": report.get("versiculos_dudosos", 0),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "estimated_cost_usd": round(estimate_cost(usage, cfg), 6),
                }
            )

        except Exception as exc:  # noqa: BLE001
            row["error"] = str(exc)

        batch_rows.append(row)

        print(
            f"[{image_name}] verificados={row['versiculos_verificados']} "
            f"corregidos={row['versiculos_corregidos']} "
            f"dudosos={row['versiculos_dudosos']} "
            f"costo_est_usd={row['estimated_cost_usd']}"
        )

    totals = {
        "processed_images": len(batch_rows),
        "successful_images": sum(1 for r in batch_rows if r["status"] == "ok"),
        "failed_images": sum(1 for r in batch_rows if r["status"] != "ok"),
        "total_versiculos_verificados": sum(r.get("versiculos_verificados", 0) for r in batch_rows),
        "total_versiculos_corregidos": sum(r.get("versiculos_corregidos", 0) for r in batch_rows),
        "total_versiculos_dudosos": sum(r.get("versiculos_dudosos", 0) for r in batch_rows),
        "total_input_tokens": sum(r.get("input_tokens", 0) for r in batch_rows),
        "total_output_tokens": sum(r.get("output_tokens", 0) for r in batch_rows),
        "total_tokens": sum(r.get("total_tokens", 0) for r in batch_rows),
        "total_estimated_cost_usd": round(sum(r.get("estimated_cost_usd", 0.0) for r in batch_rows), 6),
        "model": cfg.model,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    batch_summary_path = cfg.output_dir / "verification_costs_summary.json"
    batch_summary_path.write_text(
        json.dumps({"images": batch_rows, "totals": totals}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Resumen lote fase 2")
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    print(f"Wrote: {batch_summary_path}")


if __name__ == "__main__":
    run(build_config(parse_args()))
