#!/usr/bin/env python3
"""Extractor v5 for Biblia Torres Amat pages.

Design goals:
- Human-like semantic reading pipeline (not OCR+regex).
- Three phases: page understanding, literal extraction, targeted review.
- Preserve historical spelling and punctuation.
- Mark uncertainty instead of inventing.

Usage:
    python extractor_v5_reader_like_chatgpt.py \
      --input-dir AI156_images \
      --images AI156_0018.jpg AI156_0020.jpg AI156_0074.jpg AI156_0257.jpg
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


DEFAULT_MODEL = "gpt-4.1"
DEFAULT_TEMPERATURE = 0

# Kept configurable because pricing can change.
# Values are USD per 1M tokens.
DEFAULT_INPUT_PRICE_PER_1M = float(os.getenv("OPENAI_GPT41_INPUT_PER_1M_USD", "5.0"))
DEFAULT_OUTPUT_PRICE_PER_1M = float(os.getenv("OPENAI_GPT41_OUTPUT_PER_1M_USD", "15.0"))


PAGE_TYPES = {
    "normal_biblical_page",
    "start_of_book_mixed_page",
    "start_of_chapter_page",
    "mixed_page",
}


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class V5ReaderLikeChatGPTExtractor:
    def __init__(
        self,
        input_dir: Path,
        output_root: Path,
        model: str = DEFAULT_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
        input_price_per_1m: float = DEFAULT_INPUT_PRICE_PER_1M,
        output_price_per_1m: float = DEFAULT_OUTPUT_PRICE_PER_1M,
    ) -> None:
        self.client = OpenAI()
        self.input_dir = input_dir
        self.output_root = output_root
        self.model = model
        self.temperature = temperature
        self.input_price_per_1m = input_price_per_1m
        self.output_price_per_1m = output_price_per_1m
        self.usage = UsageTotals()

        self.page_analysis_dir = self.output_root / "page_analysis"
        self.verses_raw_dir = self.output_root / "verses_raw"
        self.verses_final_dir = self.output_root / "verses_final"
        self.review_reports_dir = self.output_root / "review_reports"
        self.logs_dir = self.output_root / "logs"

        for d in [
            self.output_root,
            self.page_analysis_dir,
            self.verses_raw_dir,
            self.verses_final_dir,
            self.review_reports_dir,
            self.logs_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    def process_batch(self, image_names: List[str]) -> Dict[str, Any]:
        processed_pages = 0
        successful_pages = 0
        successful_image_names: List[str] = []
        failed_pages: List[Dict[str, str]] = []

        for image_name in image_names:
            processed_pages += 1
            try:
                self.process_single_page(image_name)
                successful_pages += 1
                successful_image_names.append(image_name)
            except Exception as exc:  # noqa: BLE001
                failed_pages.append({"imagen_origen": image_name, "error": str(exc)})

        report_pages = self._build_manual_review_report(successful_image_names, failed_pages)
        self._write_json(self.logs_dir / "review_report.json", report_pages)
        self._write_markdown_review_report(self.logs_dir / "review_report.md", report_pages)

        summary = {
            "processed_pages": processed_pages,
            "successful_pages": successful_pages,
            "failed_pages": failed_pages,
            "input_tokens": self.usage.input_tokens,
            "output_tokens": self.usage.output_tokens,
            "total_tokens": self.usage.total_tokens,
            "estimated_cost_usd": round(self._estimate_cost_usd(), 6),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._write_json(self.logs_dir / "batch_summary.json", summary)
        return summary

    def process_single_page(self, image_name: str) -> None:
        image_path = self.input_dir / image_name
        if not image_path.exists():
            raise FileNotFoundError(f"No existe la imagen: {image_path}")

        page_id = image_path.stem

        page_analysis = self.phase_1_page_understanding(image_path)
        self._write_json(self.page_analysis_dir / f"{page_id}.json", page_analysis)

        verses_raw = self.phase_2_literal_extraction(image_path, page_analysis)
        self._write_json(self.verses_raw_dir / f"{page_id}.json", verses_raw)

        verses_final, review_report = self.phase_3_targeted_review(
            image_path=image_path,
            page_analysis=page_analysis,
            verses_raw=verses_raw,
        )
        self._write_json(self.verses_final_dir / f"{page_id}.json", verses_final)
        self._write_json(self.review_reports_dir / f"{page_id}.json", review_report)

    def phase_1_page_understanding(self, image_path: Path) -> Dict[str, Any]:
        schema_hint = {
            "imagen_origen": image_path.name,
            "libro": "string|null",
            "capitulo": "int|null",
            "tipo_pagina": "normal_biblical_page|start_of_book_mixed_page|start_of_chapter_page|mixed_page",
            "pagina_mixta": "bool",
            "columnas_biblicas": "1|2|null",
            "tiene_notas_al_pie": "bool",
            "tiene_ornamento_central": "bool",
            "tiene_resumen_capitulo": "bool",
            "inicio_texto_biblico_detectado": "string",
            "fin_texto_biblico_detectado": "string",
            "observaciones": ["string"],
        }

        prompt = (
            "Eres lector experto de Biblia histórica en español. "
            "Fase 1: comprender estructura, NO transcribir versículos completos. "
            "Devuelve SOLO JSON válido con este esquema aproximado: "
            f"{json.dumps(schema_hint, ensure_ascii=False)}"
        )

        payload = self._call_json_with_retry(
            image_path=image_path,
            prompt=prompt,
            phase_name="phase1_page_understanding",
            page_id=image_path.stem,
        )

        payload["imagen_origen"] = image_path.name
        payload.setdefault("observaciones", [])

        if payload.get("tipo_pagina") not in PAGE_TYPES:
            payload["tipo_pagina"] = "mixed_page"
            payload["observaciones"].append("tipo_pagina_ajustado_por_validacion")

        if payload.get("columnas_biblicas") not in (1, 2, None):
            payload["columnas_biblicas"] = None
            payload["observaciones"].append("columnas_biblicas_ajustado_por_validacion")

        return payload

    def phase_2_literal_extraction(
        self,
        image_path: Path,
        page_analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        schema_hint = {
            "pagina": image_path.stem,
            "versiculos": [
                {
                    "versiculo": "int",
                    "texto": "string literal visible",
                    "dudoso": "bool",
                }
            ],
        }

        prompt = (
            "Eres copista literal de Biblia histórica. "
            "Copia solo texto bíblico visible en orden natural: columna izquierda arriba-abajo y luego derecha. "
            "No modernices, no mejores, no reconstruyas faltantes, no incluyas notas al pie, "
            "no incluyas resúmenes editoriales, no mezcles columnas. "
            "Si hay incertidumbre visual, incluye el versículo y marca dudoso=true. "
            "Devuelve SOLO JSON válido con esquema: "
            f"{json.dumps(schema_hint, ensure_ascii=False)}. "
            f"Contexto de página: {json.dumps(page_analysis, ensure_ascii=False)}"
        )

        payload = self._call_json_with_retry(
            image_path=image_path,
            prompt=prompt,
            phase_name="phase2_literal_extraction",
            page_id=image_path.stem,
        )

        payload["pagina"] = image_path.stem
        payload["versiculos"] = self._normalize_verses(payload.get("versiculos", []))
        return payload

    def phase_3_targeted_review(
        self,
        image_path: Path,
        page_analysis: Dict[str, Any],
        verses_raw: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        raw_verses = self._normalize_verses(verses_raw.get("versiculos", []))
        candidates = self._detect_review_candidates(raw_verses)

        review_report: Dict[str, Any] = {
            "imagen_origen": image_path.name,
            "total_versiculos": len(raw_verses),
            "versiculos_revisados": [v["versiculo"] for v in candidates],
            "criterios": [
                "dudoso=true",
                "artefactos_sospechosos",
                "contaminacion_notas_probable",
                "numeracion_anomala",
                "duplicacion_textual",
            ],
            "acciones": [],
        }

        if not candidates:
            final = [self._make_final_item(image_path.name, page_analysis, v, "verificado") for v in raw_verses]
            return final, review_report

        schema_hint = {
            "revisiones": [
                {
                    "versiculo": "int",
                    "decision": "keep_as_is|minimally_correct|keep_but_doubtful",
                    "texto": "string",
                    "justificacion_breve": "string",
                }
            ]
        }

        prompt = (
            "Eres revisor mínimo de Biblia histórica. Revisa SOLO versículos candidatos. "
            "No reescribas todo. Si está razonablemente bien: keep_as_is. "
            "Si requiere ajuste, corrige solo fragmento mínimo. "
            "Si persiste duda, keep_but_doubtful. "
            "Ignora notas y ornamentos. Devuelve SOLO JSON válido. "
            f"Esquema: {json.dumps(schema_hint, ensure_ascii=False)}. "
            f"Analisis página: {json.dumps(page_analysis, ensure_ascii=False)}. "
            f"Candidatos: {json.dumps(candidates, ensure_ascii=False)}"
        )

        review_payload = self._call_json_with_retry(
            image_path=image_path,
            prompt=prompt,
            phase_name="phase3_targeted_review",
            page_id=image_path.stem,
        )

        revisions = {int(r["versiculo"]): r for r in review_payload.get("revisiones", []) if "versiculo" in r}

        final: List[Dict[str, Any]] = []
        for verse in raw_verses:
            rev = revisions.get(verse["versiculo"])
            if not rev:
                final.append(self._make_final_item(image_path.name, page_analysis, verse, "verificado"))
                continue

            decision = rev.get("decision", "keep_as_is")
            if decision == "minimally_correct":
                text = str(rev.get("texto", verse["texto"])).strip() or verse["texto"]
                item = self._make_final_item(image_path.name, page_analysis, {**verse, "texto": text}, "corregido")
            elif decision == "keep_but_doubtful":
                item = self._make_final_item(image_path.name, page_analysis, verse, "dudoso")
            else:
                item = self._make_final_item(image_path.name, page_analysis, verse, "verificado")

            final.append(item)
            review_report["acciones"].append(
                {
                    "versiculo": verse["versiculo"],
                    "decision": decision,
                    "justificacion_breve": rev.get("justificacion_breve", ""),
                }
            )

        return final, review_report

    def _call_json_with_retry(
        self,
        image_path: Path,
        prompt: str,
        phase_name: str,
        page_id: str,
    ) -> Dict[str, Any]:
        first_text = self._call_responses_api(image_path, prompt, phase_name, page_id, attempt=1)
        parsed = self._parse_json_safely(first_text)
        if parsed is not None:
            return parsed

        retry_prompt = (
            prompt
            + "\n\nREINTENTO ESTRICTO: responde SOLO un objeto JSON válido, sin markdown, sin comentarios, sin texto adicional."
        )
        second_text = self._call_responses_api(image_path, retry_prompt, phase_name, page_id, attempt=2)
        parsed_retry = self._parse_json_safely(second_text)
        if parsed_retry is None:
            raise ValueError(f"No se pudo parsear JSON tras reintento en {phase_name}:{page_id}")
        return parsed_retry

    def _call_responses_api(
        self,
        image_path: Path,
        prompt: str,
        phase_name: str,
        page_id: str,
        attempt: int,
    ) -> str:
        data_uri = self._image_to_data_uri(image_path)

        response = self.client.responses.create(
            model=self.model,
            temperature=self.temperature,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": data_uri,
                            "detail": "high",
                        },
                    ],
                }
            ],
        )

        self._accumulate_usage(response)
        raw_dump_path = self.logs_dir / f"{page_id}_{phase_name}_attempt{attempt}_raw.json"
        self._write_json(raw_dump_path, response.model_dump())

        text = getattr(response, "output_text", "")
        if text:
            return text

        # Conservative fallback for SDK variants.
        try:
            return json.dumps(response.model_dump(), ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return str(response)

    def _detect_review_candidates(self, verses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        seen_texts: set[str] = set()
        expected_next: Optional[int] = None

        for v in verses:
            verse_no = v["versiculo"]
            text = v["texto"]
            reasons: List[str] = []

            if v.get("dudoso", False):
                reasons.append("dudoso=true")

            if re.search(r"\b\d{2,3}\b", text):
                reasons.append("contaminacion_notas_probable")

            if re.search(r"[\[\]{}<>]|_{2,}|\.{3,}", text):
                reasons.append("artefactos_sospechosos")

            if expected_next is not None and verse_no != expected_next:
                reasons.append("numeracion_anomala")
            expected_next = verse_no + 1

            norm_text = re.sub(r"\s+", " ", text.strip().lower())
            if norm_text in seen_texts:
                reasons.append("duplicacion_textual")
            seen_texts.add(norm_text)

            if reasons:
                candidates.append(
                    {
                        "versiculo": verse_no,
                        "texto": text,
                        "dudoso": bool(v.get("dudoso", False)),
                        "razones": reasons,
                    }
                )

        return candidates

    def _build_manual_review_report(
        self,
        image_names: List[str],
        failed_pages: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        report_pages: List[Dict[str, Any]] = []

        for image_name in image_names:
            page_id = Path(image_name).stem
            page_analysis = self._load_json(self.page_analysis_dir / f"{page_id}.json", {})
            verses_raw_payload = self._load_json(self.verses_raw_dir / f"{page_id}.json", {"versiculos": []})
            verses_final = self._load_json(self.verses_final_dir / f"{page_id}.json", [])

            raw_verses = self._normalize_verses(verses_raw_payload.get("versiculos", []))
            raw_by_verse: Dict[int, List[Dict[str, Any]]] = {}
            for rv in raw_verses:
                raw_by_verse.setdefault(rv["versiculo"], []).append(rv)

            final_by_verse: Dict[int, List[Dict[str, Any]]] = {}
            if isinstance(verses_final, list):
                for fv in verses_final:
                    if not isinstance(fv, dict):
                        continue
                    try:
                        verse_no = int(fv.get("versiculo"))
                    except Exception:  # noqa: BLE001
                        continue
                    final_by_verse.setdefault(verse_no, []).append(fv)

            candidate_reasons_by_verse = {
                item["versiculo"]: item.get("razones", [])
                for item in self._detect_review_candidates(raw_verses)
                if "versiculo" in item
            }
            duplicate_numbers = {
                verse_no
                for verse_no, entries in raw_by_verse.items()
                if len(entries) > 1
            }

            all_verse_numbers = sorted(set(raw_by_verse) | set(final_by_verse))
            flagged_verses: List[Dict[str, Any]] = []
            for verse_no in all_verse_numbers:
                raw_items = raw_by_verse.get(verse_no, [])
                final_items = final_by_verse.get(verse_no, [])

                motivos: List[str] = []
                if any(bool(item.get("dudoso", False)) for item in raw_items):
                    motivos.append("dudoso")

                if any(str(item.get("estado", "")).strip() == "corregido" for item in final_items):
                    motivos.append("corregido")

                if "numeracion_anomala" in candidate_reasons_by_verse.get(verse_no, []):
                    motivos.append("numeracion_anomala")

                if verse_no in duplicate_numbers:
                    motivos.append("versiculo_duplicado")

                if not motivos:
                    continue

                flagged_verses.append(
                    {
                        "versiculo": verse_no,
                        "texto_raw": raw_items[0]["texto"] if raw_items else None,
                        "texto_final": str(final_items[0].get("texto", "")).strip() if final_items else None,
                        "motivo": motivos,
                    }
                )

            if not flagged_verses:
                continue

            report_pages.append(
                {
                    "imagen_origen": image_name,
                    "libro": page_analysis.get("libro"),
                    "capitulo": page_analysis.get("capitulo"),
                    "total_versiculos": len(raw_verses),
                    "versiculos_a_revisar": flagged_verses,
                }
            )

        for failed in failed_pages:
            image_name = failed.get("imagen_origen", "desconocido")
            page_id = Path(image_name).stem
            page_analysis = self._load_json(self.page_analysis_dir / f"{page_id}.json", {})
            verses_raw_payload = self._load_json(self.verses_raw_dir / f"{page_id}.json", {"versiculos": []})
            raw_verses = self._normalize_verses(verses_raw_payload.get("versiculos", []))

            report_pages.append(
                {
                    "imagen_origen": image_name,
                    "libro": page_analysis.get("libro"),
                    "capitulo": page_analysis.get("capitulo"),
                    "total_versiculos": len(raw_verses),
                    "versiculos_a_revisar": [
                        {
                            "versiculo": None,
                            "texto_raw": None,
                            "texto_final": None,
                            "motivo": ["fallo_parcial_pagina"],
                        }
                    ],
                }
            )

        return report_pages

    @staticmethod
    def _load_json(path: Path, fallback: Any) -> Any:
        if not path.exists():
            return fallback
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_markdown_review_report(path: Path, report_pages: List[Dict[str, Any]]) -> None:
        lines = ["# Review report", ""]
        for page in report_pages:
            image_name = str(page.get("imagen_origen", "desconocido"))
            libro = page.get("libro")
            capitulo = page.get("capitulo")
            title_right = f"{libro} {capitulo}" if libro is not None and capitulo is not None else "sin referencia"
            lines.append(f"## {image_name} — {title_right}")
            for verse in page.get("versiculos_a_revisar", []):
                motivos = ", ".join(verse.get("motivo", []))
                lines.append(f"- v{verse.get('versiculo')} — {motivos}")
            lines.append("")

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")

    def _normalize_verses(self, verses: Any) -> List[Dict[str, Any]]:
        norm: List[Dict[str, Any]] = []
        if not isinstance(verses, list):
            return norm

        for item in verses:
            if not isinstance(item, dict):
                continue
            try:
                verse_no = int(item.get("versiculo"))
            except Exception:  # noqa: BLE001
                continue
            text = str(item.get("texto", "")).strip()
            if not text:
                continue
            norm.append(
                {
                    "versiculo": verse_no,
                    "texto": text,
                    "dudoso": bool(item.get("dudoso", False)),
                }
            )
        norm.sort(key=lambda x: x["versiculo"])
        return norm

    def _make_final_item(
        self,
        image_name: str,
        page_analysis: Dict[str, Any],
        verse: Dict[str, Any],
        estado: str,
    ) -> Dict[str, Any]:
        return {
            "imagen_origen": image_name,
            "libro": page_analysis.get("libro"),
            "capitulo": page_analysis.get("capitulo"),
            "versiculo": verse["versiculo"],
            "texto": verse["texto"],
            "estado": estado,
        }

    def _estimate_cost_usd(self) -> float:
        input_cost = (self.usage.input_tokens / 1_000_000) * self.input_price_per_1m
        output_cost = (self.usage.output_tokens / 1_000_000) * self.output_price_per_1m
        return input_cost + output_cost

    def _accumulate_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        self.usage.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        self.usage.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

    @staticmethod
    def _parse_json_safely(text: str) -> Optional[Dict[str, Any]]:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

        # Try to salvage first JSON object in free-form text.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _image_to_data_uri(image_path: Path) -> str:
        mime = "image/jpeg"
        suffix = image_path.suffix.lower()
        if suffix == ".png":
            mime = "image/png"
        with image_path.open("rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime};base64,{b64}"

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extractor v5 reader-like-ChatGPT para Biblia Torres Amat"
    )
    parser.add_argument("--input-dir", default="AI156_images", help="Carpeta con imágenes de entrada")
    parser.add_argument(
        "--output-root",
        default="output/v5",
        help="Raíz de salida para page_analysis, verses_raw, verses_final, review_reports, logs",
    )
    parser.add_argument(
        "--images",
        nargs="*",
        default=["AI156_0018.jpg", "AI156_0020.jpg", "AI156_0074.jpg", "AI156_0257.jpg"],
        help="Lista de imágenes a procesar",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Páginas a procesar: números/rangos separados por coma (ej: 18,20,74,100-125)",
    )
    parser.add_argument(
        "--page-file",
        default=None,
        help="Ruta a .txt con páginas/rangos (una por línea, también admite comas)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    return parser.parse_args()


def _parse_pages_spec(spec: str) -> List[int]:
    pages: List[int] = []
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s.strip())
            end = int(end_s.strip())
            if start <= 0 or end <= 0:
                raise ValueError(f"Rango inválido (solo enteros positivos): {part}")
            if start > end:
                raise ValueError(f"Rango inválido (inicio > fin): {part}")
            pages.extend(range(start, end + 1))
        else:
            page = int(part)
            if page <= 0:
                raise ValueError(f"Página inválida (solo enteros positivos): {part}")
            pages.append(page)
    return pages


def _parse_pages_from_file(path: Path) -> List[int]:
    pages: List[int] = []
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            pages.extend(_parse_pages_spec(line))
    return pages


def _pages_to_filenames(pages: List[int]) -> List[str]:
    ordered_unique_pages = sorted(set(pages))
    return [f"AI156_{page:04d}.jpg" for page in ordered_unique_pages]


def _resolve_images_to_process(args: argparse.Namespace) -> List[str]:
    if not args.pages and not args.page_file:
        return args.images

    selected_pages: List[int] = []
    if args.pages:
        selected_pages.extend(_parse_pages_spec(args.pages))
    if args.page_file:
        selected_pages.extend(_parse_pages_from_file(Path(args.page_file)))

    return _pages_to_filenames(selected_pages)


def main() -> None:
    args = parse_args()
    candidate_images = _resolve_images_to_process(args)
    input_dir = Path(args.input_dir)

    final_images: List[str] = []
    for image_name in candidate_images:
        image_path = input_dir / image_name
        if image_path.exists():
            final_images.append(image_name)
        else:
            print(f"Advertencia: no existe {image_name} en {input_dir}")

    print("Imágenes a procesar:")
    for image_name in final_images:
        print(f"- {image_name}")

    extractor = V5ReaderLikeChatGPTExtractor(
        input_dir=input_dir,
        output_root=Path(args.output_root),
        model=args.model,
        temperature=args.temperature,
    )
    summary = extractor.process_batch(final_images)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
