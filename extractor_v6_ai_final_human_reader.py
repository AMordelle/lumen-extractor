#!/usr/bin/env python3
"""Extractor v6: human-like Bible page reader for Lumen project.

Pipeline (mandatory):
1) Semantic page understanding.
2) Human-like literal transcription in reading flow.
3) Coverage validation against expected visible verses.
4) Minimal targeted review on doubtful/problematic verses.

Designed for pages:
- AI156_0018
- AI156_0020
- AI156_0074
- AI156_0257
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
DEFAULT_TEMPERATURE = 0.0
DEFAULT_INPUT_PRICE_PER_1M = float(os.getenv("OPENAI_GPT41_INPUT_PER_1M_USD", "5.0"))
DEFAULT_OUTPUT_PRICE_PER_1M = float(os.getenv("OPENAI_GPT41_OUTPUT_PER_1M_USD", "15.0"))

ALLOWED_PAGE_TYPES = {"normal", "mixta", "inicio_libro", "inicio_capitulo"}
VALIDATION_PAGES = {"AI156_0018", "AI156_0020", "AI156_0074", "AI156_0257"}


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class V6HumanReaderExtractor:
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

        self.stage1_dir = self.output_root / "stage1_page_understanding"
        self.stage2_dir = self.output_root / "stage2_transcription_raw"
        self.stage3_dir = self.output_root / "stage3_coverage_validation"
        self.stage4_dir = self.output_root / "stage4_final"
        self.report_dir = self.output_root / "manual_review_reports"
        self.logs_dir = self.output_root / "logs"

        for d in [
            self.output_root,
            self.stage1_dir,
            self.stage2_dir,
            self.stage3_dir,
            self.stage4_dir,
            self.report_dir,
            self.logs_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    def process_batch(self, image_names: List[str]) -> Dict[str, Any]:
        processed_pages = 0
        successful_pages = 0
        failed_pages: List[Dict[str, str]] = []

        for image_name in image_names:
            processed_pages += 1
            try:
                self.process_single_page(image_name)
                successful_pages += 1
            except Exception as exc:  # noqa: BLE001
                failed_pages.append({"imagen_origen": image_name, "error": str(exc)})

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

        stage1 = self.stage_1_semantic_page_understanding(image_path)
        self._write_json(self.stage1_dir / f"{page_id}.json", stage1)

        stage2 = self.stage_2_human_like_transcription(image_path, stage1)
        self._write_json(self.stage2_dir / f"{page_id}.json", stage2)

        stage3 = self.stage_3_coverage_validation(stage1, stage2)
        self._write_json(self.stage3_dir / f"{page_id}.json", stage3)

        stage4_final, report = self.stage_4_minimal_targeted_review(
            image_path=image_path,
            stage1=stage1,
            stage2=stage2,
            stage3=stage3,
        )
        self._write_json(self.stage4_dir / f"{page_id}.json", stage4_final)
        self._write_json(self.report_dir / f"{page_id}.json", report)

    def stage_1_semantic_page_understanding(self, image_path: Path) -> Dict[str, Any]:
        schema_hint = {
            "pagina": image_path.stem,
            "libro": "string|null",
            "capitulo_principal": "int|null",
            "hay_transicion_capitulo": "bool",
            "capitulo_transicion_hacia": "int|null",
            "tipo_pagina": "normal|mixta|inicio_libro|inicio_capitulo",
            "presencia_notas_al_pie": "bool",
            "presencia_ornamentos": "bool",
            "columnas": "1|2",
            "contenido_columna_izquierda": "string",
            "contenido_columna_derecha": "string",
            "versiculos_visibles_aproximados": [
                {"capitulo": "int", "desde": "int", "hasta": "int"}
            ],
            "primer_versiculo_visible": {"capitulo": "int", "versiculo": "int"},
            "ultimo_versiculo_visible": {"capitulo": "int", "versiculo": "int"},
            "observaciones": ["string"],
        }

        prompt = (
            "Fase 1 (obligatoria): comprensión semántica de página bíblica impresa, NO OCR, NO transcripción completa. "
            "Devuelve SOLO JSON válido. "
            "Debes separar contenido bíblico de no bíblico (resúmenes, notas, ornamentos), detectar orden de lectura humano "
            "(columna izquierda y luego derecha), detectar transición de capítulo si existe, y estimar cobertura visible. "
            "No uses coordenadas. "
            f"Esquema esperado: {json.dumps(schema_hint, ensure_ascii=False)}"
        )

        payload = self._call_json_strict(
            image_path=image_path,
            prompt=prompt,
            phase_name="stage1_semantic_page_understanding",
            page_id=image_path.stem,
        )

        payload["pagina"] = image_path.stem
        payload.setdefault("observaciones", [])

        if payload.get("tipo_pagina") not in ALLOWED_PAGE_TYPES:
            payload["tipo_pagina"] = "mixta"
            payload["observaciones"].append("tipo_pagina_ajustado_por_validacion")

        if payload.get("columnas") not in (1, 2):
            payload["columnas"] = 2
            payload["observaciones"].append("columnas_ajustado_por_validacion")

        payload["versiculos_visibles_aproximados"] = self._normalize_expected_ranges(
            payload.get("versiculos_visibles_aproximados", [])
        )
        payload["primer_versiculo_visible"] = self._normalize_reference(payload.get("primer_versiculo_visible"))
        payload["ultimo_versiculo_visible"] = self._normalize_reference(payload.get("ultimo_versiculo_visible"))
        return payload

    def stage_2_human_like_transcription(self, image_path: Path, stage1: Dict[str, Any]) -> Dict[str, Any]:
        schema_hint = {
            "pagina": image_path.stem,
            "versiculos": [
                {
                    "capitulo": "int",
                    "versiculo": "int",
                    "texto": "string_literal_visible",
                    "dudoso": "bool",
                }
            ],
        }

        prompt = (
            "Fase 2 (obligatoria): transcripción literal humana de texto bíblico visible. "
            "Reglas estrictas: no parafrasear, no modernizar ortografía histórica, no mejorar fluidez, no reconstruir agresivamente. "
            "Si hay duda visual, conserva texto visible y marca dudoso=true. "
            "No incluir notas al pie, ornamentos, encabezados ni resúmenes no bíblicos. "
            "Orden de lectura: columna izquierda arriba→abajo y luego columna derecha. "
            "Soporta transición de capítulo en una misma página. "
            "Devuelve SOLO JSON válido con esquema: "
            f"{json.dumps(schema_hint, ensure_ascii=False)}. "
            f"Mapa semántico de Fase 1: {json.dumps(stage1, ensure_ascii=False)}"
        )

        payload = self._call_json_strict(
            image_path=image_path,
            prompt=prompt,
            phase_name="stage2_human_like_transcription",
            page_id=image_path.stem,
        )
        payload["pagina"] = image_path.stem
        payload["versiculos"] = self._normalize_extracted_verses(payload.get("versiculos", []))
        return payload

    def stage_3_coverage_validation(self, stage1: Dict[str, Any], stage2: Dict[str, Any]) -> Dict[str, Any]:
        expected_refs = self._expand_expected_refs(stage1)
        extracted = stage2.get("versiculos", [])
        extracted_refs = [self._ref_key(v["capitulo"], v["versiculo"]) for v in extracted]

        extracted_set = set(extracted_refs)
        missing = [ref for ref in expected_refs if ref not in extracted_set]

        flags = {
            "cobertura_incompleta": len(missing) > 0,
            "versiculos_faltantes_probables": missing,
            "transicion_capitulo_mal_resuelta": self._is_transition_mishandled(stage1, extracted),
        }

        note_contamination = self._detect_note_contamination(extracted)
        duplicated_fragments = self._detect_duplicate_fragments(extracted)

        return {
            "pagina": stage2.get("pagina"),
            "esperado_aproximado": expected_refs,
            "extraido": extracted_refs,
            "flags_pagina": flags,
            "contaminacion_notas_detectada": note_contamination,
            "fragmentos_duplicados_detectados": duplicated_fragments,
        }

    def stage_4_minimal_targeted_review(
        self,
        image_path: Path,
        stage1: Dict[str, Any],
        stage2: Dict[str, Any],
        stage3: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        raw_verses = stage2.get("versiculos", [])
        candidates = self._review_candidates(raw_verses, stage3)

        review_results: Dict[str, Dict[str, Any]] = {}
        if candidates:
            schema_hint = {
                "revisiones": [
                    {
                        "capitulo": "int",
                        "versiculo": "int",
                        "estado": "verificado|corregido|dudoso",
                        "texto": "string",
                        "justificacion_breve": "string",
                    }
                ]
            }
            prompt = (
                "Fase 4 (obligatoria): revisión mínima y dirigida. "
                "Revisa SOLO versículos candidatos o claramente corruptos. "
                "Prioriza fidelidad literal sobre fluidez. No reescribas versículos completos sin necesidad. "
                "Estados permitidos: verificado, corregido, dudoso. "
                "Devuelve SOLO JSON válido. "
                f"Esquema: {json.dumps(schema_hint, ensure_ascii=False)}. "
                f"Contexto Fase 1: {json.dumps(stage1, ensure_ascii=False)}. "
                f"Contexto Fase 3: {json.dumps(stage3, ensure_ascii=False)}. "
                f"Candidatos: {json.dumps(candidates, ensure_ascii=False)}"
            )
            review_payload = self._call_json_strict(
                image_path=image_path,
                prompt=prompt,
                phase_name="stage4_minimal_targeted_review",
                page_id=image_path.stem,
            )

            for rev in review_payload.get("revisiones", []):
                ref = self._ref_key(int(rev["capitulo"]), int(rev["versiculo"]))
                review_results[ref] = rev

        final_verses: List[Dict[str, Any]] = []
        verse_issues: List[Dict[str, Any]] = []

        for verse in raw_verses:
            ref = self._ref_key(verse["capitulo"], verse["versiculo"])
            rev = review_results.get(ref)
            final_text = verse["texto"]
            estado = "dudoso" if verse.get("dudoso", False) else "verificado"

            if rev:
                estado = rev.get("estado", estado)
                if estado == "corregido":
                    final_text = str(rev.get("texto", final_text)).strip() or final_text
                elif estado == "dudoso":
                    estado = "dudoso"
                else:
                    estado = "verificado"

                verse_issues.append(
                    {
                        "capitulo": verse["capitulo"],
                        "versiculo": verse["versiculo"],
                        "estado": estado,
                        "justificacion": rev.get("justificacion_breve", ""),
                    }
                )
            elif verse.get("dudoso", False):
                verse_issues.append(
                    {
                        "capitulo": verse["capitulo"],
                        "versiculo": verse["versiculo"],
                        "estado": "dudoso",
                        "justificacion": "marcado_dudoso_en_extraccion",
                    }
                )

            final_verses.append(
                {
                    "capitulo": verse["capitulo"],
                    "versiculo": verse["versiculo"],
                    "texto": final_text,
                    "dudoso": estado == "dudoso",
                    "estado": estado,
                }
            )

        page_flags = stage3.get("flags_pagina", {})
        page_level_issues: List[Dict[str, Any]] = []

        if page_flags.get("cobertura_incompleta"):
            page_level_issues.append(
                {
                    "tipo": "cobertura_incompleta",
                    "detalle": stage3.get("flags_pagina", {}).get("versiculos_faltantes_probables", []),
                }
            )
        if page_flags.get("transicion_capitulo_mal_resuelta"):
            page_level_issues.append(
                {"tipo": "transicion_capitulo_mal_resuelta", "detalle": "capitulos_extraidos_no_coinciden"}
            )
        if stage3.get("contaminacion_notas_detectada"):
            page_level_issues.append(
                {
                    "tipo": "contaminacion_notas",
                    "detalle": stage3.get("contaminacion_notas_detectada", []),
                }
            )
        if stage3.get("fragmentos_duplicados_detectados"):
            page_level_issues.append(
                {
                    "tipo": "fragmentos_duplicados",
                    "detalle": stage3.get("fragmentos_duplicados_detectados", []),
                }
            )

        report = {
            "pagina": stage2.get("pagina"),
            "incluida_en_reporte": bool(verse_issues or page_level_issues),
            "issues_por_versiculo": verse_issues,
            "issues_por_pagina": page_level_issues,
        }

        final_payload = {
            "pagina": stage2.get("pagina"),
            "versiculos": final_verses,
            "flags_pagina": page_flags,
        }
        return final_payload, report

    def _call_json_strict(
        self,
        image_path: Path,
        prompt: str,
        phase_name: str,
        page_id: str,
    ) -> Dict[str, Any]:
        text_1 = self._call_responses_api(image_path, prompt, phase_name, page_id, attempt=1)
        parsed_1 = self._parse_json_strict(text_1)
        if parsed_1 is not None:
            return parsed_1

        retry_prompt = (
            prompt
            + "\n\nREINTENTO ESTRICTO: Responde SOLO un objeto JSON válido sin markdown, sin comentarios y sin texto extra."
        )
        text_2 = self._call_responses_api(image_path, retry_prompt, phase_name, page_id, attempt=2)
        parsed_2 = self._parse_json_strict(text_2)
        if parsed_2 is None:
            raise ValueError(f"JSON inválido en {phase_name}:{page_id}")
        return parsed_2

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
                        {"type": "input_image", "image_url": data_uri, "detail": "high"},
                    ],
                }
            ],
        )

        self._accumulate_usage(response)
        self._write_json(self.logs_dir / f"{page_id}_{phase_name}_attempt{attempt}_raw.json", response.model_dump())

        text = getattr(response, "output_text", "")
        if isinstance(text, str) and text.strip():
            return text
        return ""

    @staticmethod
    def _parse_json_strict(text: str) -> Optional[Dict[str, Any]]:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _normalize_reference(raw: Any) -> Optional[Dict[str, int]]:
        if not isinstance(raw, dict):
            return None
        try:
            cap = int(raw.get("capitulo"))
            ver = int(raw.get("versiculo"))
            if cap <= 0 or ver <= 0:
                return None
            return {"capitulo": cap, "versiculo": ver}
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _normalize_expected_ranges(ranges: Any) -> List[Dict[str, int]]:
        out: List[Dict[str, int]] = []
        if not isinstance(ranges, list):
            return out
        for item in ranges:
            if not isinstance(item, dict):
                continue
            try:
                cap = int(item.get("capitulo"))
                desde = int(item.get("desde"))
                hasta = int(item.get("hasta"))
                if cap <= 0 or desde <= 0 or hasta <= 0:
                    continue
                if desde > hasta:
                    desde, hasta = hasta, desde
                out.append({"capitulo": cap, "desde": desde, "hasta": hasta})
            except Exception:  # noqa: BLE001
                continue
        return out

    @staticmethod
    def _normalize_extracted_verses(raw_verses: Any) -> List[Dict[str, Any]]:
        verses: List[Dict[str, Any]] = []
        if not isinstance(raw_verses, list):
            return verses
        for item in raw_verses:
            if not isinstance(item, dict):
                continue
            try:
                cap = int(item.get("capitulo"))
                ver = int(item.get("versiculo"))
            except Exception:  # noqa: BLE001
                continue
            text = str(item.get("texto", "")).strip()
            if cap <= 0 or ver <= 0 or not text:
                continue
            verses.append(
                {
                    "capitulo": cap,
                    "versiculo": ver,
                    "texto": text,
                    "dudoso": bool(item.get("dudoso", False)),
                }
            )
        return verses

    def _expand_expected_refs(self, stage1: Dict[str, Any]) -> List[str]:
        refs: List[str] = []
        for r in stage1.get("versiculos_visibles_aproximados", []):
            cap = r["capitulo"]
            for v in range(r["desde"], r["hasta"] + 1):
                refs.append(self._ref_key(cap, v))

        # fallback if no ranges available
        if not refs:
            first = stage1.get("primer_versiculo_visible")
            last = stage1.get("ultimo_versiculo_visible")
            if first and last and first["capitulo"] == last["capitulo"] and first["versiculo"] <= last["versiculo"]:
                refs = [
                    self._ref_key(first["capitulo"], v)
                    for v in range(first["versiculo"], last["versiculo"] + 1)
                ]
        return refs

    @staticmethod
    def _is_transition_mishandled(stage1: Dict[str, Any], extracted: List[Dict[str, Any]]) -> bool:
        if not stage1.get("hay_transicion_capitulo", False):
            return False
        chapters = sorted({v["capitulo"] for v in extracted})
        if len(chapters) < 2:
            return True
        target = stage1.get("capitulo_transicion_hacia")
        if isinstance(target, int) and target > 0 and target not in chapters:
            return True
        return False

    @staticmethod
    def _detect_note_contamination(extracted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        contamination: List[Dict[str, Any]] = []
        for v in extracted:
            text = v["texto"]
            if "*" in text or re.search(r"\[[^\]]+\]", text) or re.search(r"\bnota\b", text, flags=re.I):
                contamination.append({"capitulo": v["capitulo"], "versiculo": v["versiculo"]})
        return contamination

    @staticmethod
    def _detect_duplicate_fragments(extracted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: Dict[str, Tuple[int, int]] = {}
        duplicates: List[Dict[str, Any]] = []
        for v in extracted:
            normalized = re.sub(r"\s+", " ", v["texto"].strip().lower())
            if not normalized:
                continue
            if normalized in seen:
                prev = seen[normalized]
                duplicates.append(
                    {
                        "capitulo": v["capitulo"],
                        "versiculo": v["versiculo"],
                        "duplica_de": {"capitulo": prev[0], "versiculo": prev[1]},
                    }
                )
            else:
                seen[normalized] = (v["capitulo"], v["versiculo"])
        return duplicates

    def _review_candidates(self, verses: List[Dict[str, Any]], stage3: Dict[str, Any]) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        flagged_page = bool(stage3.get("flags_pagina", {}).get("cobertura_incompleta")) or bool(
            stage3.get("flags_pagina", {}).get("transicion_capitulo_mal_resuelta")
        )

        for v in verses:
            reasons: List[str] = []
            if v.get("dudoso", False):
                reasons.append("dudoso=true")
            if re.search(r"[{}\[\]<>]", v["texto"]) or "..." in v["texto"]:
                reasons.append("texto_corrupto_probable")
            if flagged_page:
                reasons.append("pagina_problemática")

            if reasons:
                candidates.append(
                    {
                        "capitulo": v["capitulo"],
                        "versiculo": v["versiculo"],
                        "texto": v["texto"],
                        "dudoso": v.get("dudoso", False),
                        "razones": sorted(set(reasons)),
                    }
                )
        return candidates

    @staticmethod
    def _ref_key(capitulo: int, versiculo: int) -> str:
        return f"{capitulo}:{versiculo}"

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
    def _image_to_data_uri(path: Path) -> str:
        suffix = path.suffix.lower()
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        with path.open("rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime};base64,{b64}"

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extractor v6 human-like final")
    parser.add_argument("--input-dir", default="AI156_images")
    parser.add_argument("--output-root", default="output/v6_final")
    parser.add_argument(
        "--images",
        nargs="*",
        default=["AI156_0018.jpg", "AI156_0020.jpg", "AI156_0074.jpg", "AI156_0257.jpg"],
        help="Imágenes objetivo para validación estricta",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    selected_images: List[str] = []

    for img in args.images:
        stem = Path(img).stem
        if stem not in VALIDATION_PAGES:
            raise ValueError(
                "Validación estricta: solo se permite procesar AI156_0018, AI156_0020, AI156_0074, AI156_0257"
            )
        selected_images.append(img)

    input_dir = Path(args.input_dir)
    existing_images = [img for img in selected_images if (input_dir / img).exists()]
    missing_images = sorted(set(selected_images) - set(existing_images))

    if missing_images:
        print("Advertencia: faltan imágenes:")
        for img in missing_images:
            print(f"- {img}")

    print("Imágenes a procesar:")
    for img in existing_images:
        print(f"- {img}")

    extractor = V6HumanReaderExtractor(
        input_dir=input_dir,
        output_root=Path(args.output_root),
        model=args.model,
        temperature=args.temperature,
    )
    summary = extractor.process_batch(existing_images)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
