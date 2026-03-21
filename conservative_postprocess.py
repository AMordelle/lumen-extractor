#!/usr/bin/env python3
"""Strictly conservative post-processing for verse JSON files."""

from __future__ import annotations

import argparse
import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_IMAGES = [
    "AI156_0018.jpg",
    "AI156_0020.jpg",
    "AI156_0074.jpg",
    "AI156_0257.jpg",
]

PUNCTUATION_FIX_RE = re.compile(r"\s+([,.;:])")
MULTISPACE_RE = re.compile(r"[ \t]{2,}")
# Case-sensitive consecutive duplicate word detection.
DUPLICATED_WORD_RE = re.compile(r"\b(?P<word>[^\W\d_]+)\b\s+(?P=word)\b(?P<punct>[,.;:]?)")
# Only isolated OCR noise tokens requested by spec.
ISOLATED_NOISE_RE = re.compile(r"(^|\s)[\|~`]{1}(?=\s|$)")


@dataclass
class CleanConfig:
    verses_dir: Path = Path("output/verses_json")
    output_dir: Path = Path("output/cleaned")
    image_names: list[str] = field(default_factory=lambda: list(DEFAULT_IMAGES))
    long_verse_threshold: int = 900


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict conservative cleaner for verses JSON files")
    parser.add_argument("--verses-dir", default="output/verses_json")
    parser.add_argument("--output-dir", default="output/cleaned")
    parser.add_argument("--images", nargs="*", default=DEFAULT_IMAGES)
    parser.add_argument("--long-verse-threshold", type=int, default=900)
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> CleanConfig:
    return CleanConfig(
        verses_dir=Path(args.verses_dir),
        output_dir=Path(args.output_dir),
        image_names=list(args.images),
        long_verse_threshold=args.long_verse_threshold,
    )


def _apply_rule(text: str, pattern: re.Pattern[str], replacement: str) -> tuple[str, bool]:
    cleaned = pattern.sub(replacement, text)
    return cleaned, cleaned != text


def apply_conservative_cleaning(text: str) -> tuple[str, list[str]]:
    """Apply only strictly mechanical cleaning rules, never lexical replacements."""
    change_types: list[str] = []
    current = text

    current, changed = _apply_rule(current, MULTISPACE_RE, " ")
    if changed:
        change_types.append("espacios_duplicados")

    current, changed = _apply_rule(current, PUNCTUATION_FIX_RE, r"\1")
    if changed:
        change_types.append("espacio_antes_puntuacion")

    cleaned = re.sub(r"([,.;:])(\S)", r"\1 \2", current)
    if cleaned != current:
        change_types.append("espacio_despues_puntuacion")
    current = cleaned

    cleaned = current
    while True:
        next_cleaned = DUPLICATED_WORD_RE.sub(lambda m: f"{m.group('word')}{m.group('punct')}", cleaned)
        if next_cleaned == cleaned:
            break
        cleaned = next_cleaned
    if cleaned != current:
        change_types.append("palabra_duplicada")
    current = cleaned

    cleaned = ISOLATED_NOISE_RE.sub(" ", current)
    cleaned = MULTISPACE_RE.sub(" ", cleaned).strip()
    if cleaned != current:
        change_types.append("caracter_basura_aislado")
    current = cleaned

    return current, change_types


def detect_structural_warnings(verses: list[dict[str, Any]], long_threshold: int) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    verse_numbers: list[int] = []
    seen: set[int] = set()

    for item in verses:
        verse = item.get("versiculo")
        text = item.get("texto", "")
        image = item.get("imagen_origen", "")

        if isinstance(verse, int):
            if verse in seen:
                warnings.append(
                    {
                        "type": "versiculo_duplicado",
                        "imagen": image,
                        "versiculo": verse,
                        "message": "Versículo repetido en la misma página",
                    }
                )
            seen.add(verse)
            verse_numbers.append(verse)

        if isinstance(text, str) and len(text) > long_threshold:
            warnings.append(
                {
                    "type": "versiculo_muy_largo",
                    "imagen": image,
                    "versiculo": verse,
                    "message": f"Longitud sospechosa ({len(text)} > {long_threshold})",
                }
            )

    verse_numbers = sorted(verse_numbers)
    for idx in range(1, len(verse_numbers)):
        previous = verse_numbers[idx - 1]
        current = verse_numbers[idx]
        if current - previous > 3:
            warnings.append(
                {
                    "type": "salto_numeracion_sospechoso",
                    "imagen": verses[0].get("imagen_origen", "") if verses else "",
                    "from": previous,
                    "to": current,
                    "message": f"Salto de numeración sospechoso: {previous} -> {current}",
                }
            )

    return warnings


def process_file(image_name: str, cfg: CleanConfig) -> tuple[int, int, int, list[dict[str, Any]]]:
    stem = Path(image_name).stem
    input_path = cfg.verses_dir / f"{stem}.verses.json"
    raw_out_path = cfg.output_dir / f"{stem}.verses_raw.json"
    clean_out_path = cfg.output_dir / f"{stem}.verses_clean.json"
    report_out_path = cfg.output_dir / f"{stem}.cleaning_report.json"
    warnings_out_path = cfg.output_dir / f"{stem}.warnings.json"

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    verses = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(verses, list):
        raise ValueError(f"Expected list in {input_path}")

    raw_verses = copy.deepcopy(verses)
    cleaned_verses = copy.deepcopy(verses)

    cleaning_report: list[dict[str, Any]] = []
    changed_verses = 0
    total_changes = 0

    for verse in cleaned_verses:
        text = verse.get("texto")
        if not isinstance(text, str):
            continue

        cleaned_text, change_types = apply_conservative_cleaning(text)
        if cleaned_text != text:
            changed_verses += 1
            total_changes += len(change_types)
            verse["texto"] = cleaned_text

            for change_type in change_types:
                cleaning_report.append(
                    {
                        "imagen": verse.get("imagen_origen"),
                        "versiculo": verse.get("versiculo"),
                        "tipo_cambio": change_type,
                        "antes": text,
                        "despues": cleaned_text,
                    }
                )

    warnings = detect_structural_warnings(cleaned_verses, cfg.long_verse_threshold)

    raw_out_path.write_text(json.dumps(raw_verses, ensure_ascii=False, indent=2), encoding="utf-8")
    clean_out_path.write_text(json.dumps(cleaned_verses, ensure_ascii=False, indent=2), encoding="utf-8")
    report_out_path.write_text(json.dumps(cleaning_report, ensure_ascii=False, indent=2), encoding="utf-8")
    warnings_out_path.write_text(json.dumps(warnings, ensure_ascii=False, indent=2), encoding="utf-8")

    return changed_verses, total_changes, len(warnings), cleaning_report


def run(cfg: CleanConfig) -> None:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    total_changed_verses = 0
    total_changes = 0
    total_warnings = 0
    batch_changes: list[dict[str, Any]] = []

    for image in cfg.image_names:
        try:
            changed_verses, changes, warnings_count, file_changes = process_file(image, cfg)
            total_changed_verses += changed_verses
            total_changes += changes
            total_warnings += warnings_count
            batch_changes.extend(file_changes)
        except Exception as exc:  # noqa: BLE001
            print(f"- Error procesando {image}: {exc}")

    # Global report with applied changes only.
    (cfg.output_dir / "cleaning_report.json").write_text(
        json.dumps(batch_changes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Resumen limpieza conservadora")
    print(f"- Versículos limpiados: {total_changed_verses}")
    print(f"- Cambios aplicados: {total_changes}")
    print(f"- Advertencias estructurales: {total_warnings}")


if __name__ == "__main__":
    run(build_config(parse_args()))
