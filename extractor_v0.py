#!/usr/bin/env python3
"""Extractor v0 para validar extracción de texto bíblico desde 4 imágenes específicas."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


TARGET_IMAGES = [
    "AI156_0018.jpg",
    "AI156_0020.jpg",
    "AI156_0074.jpg",
    "AI156_0257.jpg",
]

VERSE_START_RE = re.compile(r"^(\d+)\.\s*(.+)?")
BOOK_RE = re.compile(r"LIBRO\s+DEL\s+([A-ZÁÉÍÓÚÑ\s]+)", re.IGNORECASE)
CHAPTER_RE = re.compile(r"CAP[IÍ]TULO\s+([A-Z0-9ÁÉÍÓÚÑ]+)", re.IGNORECASE)
EDITORIAL_RE = re.compile(r"^(LIBRO\s+DEL|ADVERTENCIA|CAP[IÍ]TULO)", re.IGNORECASE)


@dataclass
class ImageReport:
    file_name: str
    status: str
    columns_detected: int = 1
    book: str | None = None
    chapter: str | None = None
    observations: list[str] = field(default_factory=list)


@dataclass
class VerseRecord:
    libro: str | None
    capitulo: str | None
    versiculo: int
    texto: str
    imagen_origen: str


class TorresAmatExtractorV0:
    def __init__(self, images_dir: Path, output_dir: Path) -> None:
        try:
            import cv2  # type: ignore
            import pytesseract  # type: ignore
        except ModuleNotFoundError as exc:
            missing = exc.name or "dependencia requerida"
            raise RuntimeError(
                f"Falta la dependencia `{missing}`. Instala requirements.txt y Tesseract OCR antes de ejecutar."
            ) from exc

        self.cv2 = cv2
        self.pytesseract = pytesseract
        self.images_dir = images_dir
        self.output_dir = output_dir
        self.columns_dir = output_dir / "column_text"
        self.anomalies: list[dict[str, Any]] = []
        self.reports: list[ImageReport] = []
        self.verses: list[VerseRecord] = []

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.columns_dir.mkdir(parents=True, exist_ok=True)

        processed = 0
        success = 0
        warnings = 0

        for image_name in TARGET_IMAGES:
            image_path = self.images_dir / image_name
            processed += 1
            report = self.process_image(image_path)
            self.reports.append(report)
            if report.status == "ok":
                success += 1
            else:
                warnings += 1

        self._write_outputs()
        self._print_summary(processed, success, warnings)

    def process_image(self, image_path: Path) -> ImageReport:
        if not image_path.exists():
            obs = f"Imagen no encontrada: {image_path}"
            self._warn(image_path.name, obs)
            return ImageReport(file_name=image_path.name, status="warning", observations=[obs])

        image = self.cv2.imread(str(image_path))
        if image is None:
            obs = "No se pudo abrir la imagen (cv2.imread devolvió None)."
            self._warn(image_path.name, obs)
            return ImageReport(file_name=image_path.name, status="warning", observations=[obs])

        columns = self._split_columns(image)
        report = ImageReport(file_name=image_path.name, status="ok", columns_detected=len(columns))

        if len(columns) < 2:
            report.status = "warning"
            obs = "No se detectaron claramente dos columnas."
            report.observations.append(obs)
            self._warn(image_path.name, obs)

        all_lines: list[str] = []
        for idx, column_img in enumerate(columns, start=1):
            col_lines = self._ocr_column_lines(column_img)
            cleaned_lines = self._exclude_footnotes(col_lines)
            self._save_column_text(image_path.name, idx, cleaned_lines)
            all_lines.extend(cleaned_lines)

        book, chapter = self._extract_metadata(all_lines)
        report.book = book
        report.chapter = chapter

        if book is None:
            obs = "Libro no detectado en cabecera."
            report.observations.append(obs)
            self._warn(image_path.name, obs)
        if chapter is None:
            obs = "Capítulo no detectado en cabecera."
            report.observations.append(obs)
            self._warn(image_path.name, obs)

        verses, verse_obs = self._extract_verses(all_lines, image_path.name, book, chapter)
        self.verses.extend(verses)
        report.observations.extend(verse_obs)

        if not verses:
            report.status = "warning"
            obs = "No se detectaron versículos con patrón número+punto."
            report.observations.append(obs)
            self._warn(image_path.name, obs)

        return report

    def _split_columns(self, image: Any) -> list[Any]:
        gray = self.cv2.cvtColor(image, self.cv2.COLOR_BGR2GRAY)
        _, binary = self.cv2.threshold(gray, 190, 255, self.cv2.THRESH_BINARY_INV)
        vertical_density = binary.sum(axis=0)
        width = image.shape[1]
        center = width // 2

        search_start = max(0, center - width // 6)
        search_end = min(width, center + width // 6)
        valley_idx = int(vertical_density[search_start:search_end].argmin() + search_start)

        min_side = width * 0.25
        if valley_idx < min_side or (width - valley_idx) < min_side:
            return [image]

        left = image[:, :valley_idx]
        right = image[:, valley_idx:]
        return [left, right]

    def _ocr_column_lines(self, column_img: Any) -> list[str]:
        gray = self.cv2.cvtColor(column_img, self.cv2.COLOR_BGR2GRAY)
        upscaled = self.cv2.resize(gray, None, fx=1.8, fy=1.8, interpolation=self.cv2.INTER_CUBIC)
        denoised = self.cv2.GaussianBlur(upscaled, (3, 3), 0)
        _, bw = self.cv2.threshold(denoised, 0, 255, self.cv2.THRESH_BINARY + self.cv2.THRESH_OTSU)

        text = self.pytesseract.image_to_string(bw, lang="spa", config="--psm 4")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines

    def _exclude_footnotes(self, lines: list[str]) -> list[str]:
        if not lines:
            return lines

        lower_block_start = int(len(lines) * 0.80)
        filtered: list[str] = []

        for idx, line in enumerate(lines):
            looks_like_footnote = idx >= lower_block_start and bool(re.match(r"^\d+[\)\.]?\s+[a-záéíóúñ]", line))
            short_footnote_like = idx >= lower_block_start and len(line) < 25 and bool(re.match(r"^[a-záéíóúñ].*", line))

            if looks_like_footnote or short_footnote_like:
                continue
            filtered.append(line)

        return filtered

    def _extract_metadata(self, lines: list[str]) -> tuple[str | None, str | None]:
        joined = "\n".join(lines)
        book_match = BOOK_RE.search(joined)
        chapter_match = CHAPTER_RE.search(joined)

        book = book_match.group(1).strip().title() if book_match else None
        chapter = chapter_match.group(1).strip() if chapter_match else None
        return book, chapter

    def _extract_verses(
        self,
        lines: list[str],
        image_name: str,
        book: str | None,
        chapter: str | None,
    ) -> tuple[list[VerseRecord], list[str]]:
        verses: list[VerseRecord] = []
        observations: list[str] = []
        current_num: int | None = None
        current_parts: list[str] = []
        started = False

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            if not started and EDITORIAL_RE.match(line):
                observations.append("Bloque editorial detectado y omitido al inicio.")
                continue

            match = VERSE_START_RE.match(line)
            if match:
                started = True
                if current_num is not None:
                    verse_text = " ".join(current_parts).strip()
                    if verse_text:
                        verses.append(
                            VerseRecord(
                                libro=book,
                                capitulo=chapter,
                                versiculo=current_num,
                                texto=verse_text,
                                imagen_origen=image_name,
                            )
                        )
                current_num = int(match.group(1))
                first_text = (match.group(2) or "").strip()
                current_parts = [first_text] if first_text else []
                continue

            if started and current_num is not None:
                current_parts.append(line)

        if current_num is not None:
            verse_text = " ".join(current_parts).strip()
            if verse_text:
                verses.append(
                    VerseRecord(
                        libro=book,
                        capitulo=chapter,
                        versiculo=current_num,
                        texto=verse_text,
                        imagen_origen=image_name,
                    )
                )

        for verse in verses:
            if len(verse.texto) > 600:
                obs = f"Versículo {verse.versiculo} demasiado largo; posible mezcla de columnas."
                observations.append(obs)
                self._warn(image_name, obs)

        return verses, observations

    def _save_column_text(self, image_name: str, column_index: int, lines: list[str]) -> None:
        output_path = self.columns_dir / f"{Path(image_name).stem}_col{column_index}.txt"
        output_path.write_text("\n".join(lines), encoding="utf-8")

    def _warn(self, image_name: str, message: str) -> None:
        self.anomalies.append({"imagen": image_name, "warning": message})

    def _write_outputs(self) -> None:
        report_json = self.output_dir / "image_report.json"
        verses_json = self.output_dir / "verses.json"
        anomalies_json = self.output_dir / "anomalies.json"

        report_json.write_text(
            json.dumps([asdict(r) for r in self.reports], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        verses_json.write_text(
            json.dumps([asdict(v) for v in self.verses], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        anomalies_json.write_text(json.dumps(self.anomalies, ensure_ascii=False, indent=2), encoding="utf-8")

    def _print_summary(self, processed: int, success: int, warnings: int) -> None:
        print("=== Extractor v0 - Resumen de ejecución ===")
        print(f"Imágenes procesadas: {processed}")
        print(f"Imágenes exitosas: {success}")
        print(f"Imágenes con advertencias: {warnings}")
        print()
        print("=== Reporte por imagen ===")

        for report in self.reports:
            print(f"- Archivo: {report.file_name}")
            print(f"  Libro: {report.book or 'N/D'}")
            print(f"  Capítulo: {report.chapter or 'N/D'}")
            print(f"  Columnas detectadas: {report.columns_detected}")
            if report.observations:
                print(f"  Observaciones: {' | '.join(sorted(set(report.observations)))}")
            else:
                print("  Observaciones: ninguna")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extractor v0 Torres Amat (4 imágenes)")
    parser.add_argument("--images-dir", default="AI156_images", help="Carpeta con imágenes de entrada")
    parser.add_argument("--output-dir", default="output_v0", help="Carpeta de salida")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        extractor = TorresAmatExtractorV0(Path(args.images_dir), Path(args.output_dir))
        extractor.run()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
