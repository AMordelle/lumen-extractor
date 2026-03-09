#!/usr/bin/env python3
"""Extractor v0 (iteración 2) para validar OCR bíblico en 4 imágenes Torres Amat."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any


TARGET_IMAGES = [
    "AI156_0018.jpg",
    "AI156_0020.jpg",
    "AI156_0074.jpg",
    "AI156_0257.jpg",
]

VERSE_START_RE = re.compile(r"^(\d{1,3})\.\s*(.+)?$")
BOOK_RE = re.compile(r"\bLIBRO\s+DEL\s+([A-ZÁÉÍÓÚÑ\s]{3,})")
CHAPTER_RE = re.compile(r"\bCAP[IÍ]TULO\s+([A-Z0-9ÁÉÍÓÚÑ]+)")
EDITORIAL_RE = re.compile(r"^(LIBRO\s+DEL|ADVERTENCIA|CAP[IÍ]TULO)\b", re.IGNORECASE)

KNOWN_BOOKS = {
    "GENESIS": "Génesis",
    "EXODO": "Éxodo",
    "LEVITICO": "Levítico",
    "NUMEROS": "Números",
    "DEUTERONOMIO": "Deuteronomio",
}


@dataclass
class OcrToken:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float


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
            import pytesseract  # type: ignore
            from PIL import Image, ImageOps  # type: ignore
        except ModuleNotFoundError as exc:
            missing = exc.name or "dependencia requerida"
            raise RuntimeError(
                f"Falta la dependencia `{missing}`. Instala requirements.txt y Tesseract OCR antes de ejecutar."
            ) from exc

        self.pytesseract = pytesseract
        self.Image = Image
        self.ImageOps = ImageOps
        self.images_dir = images_dir
        self.output_dir = output_dir
        self.columns_dir = output_dir / "column_text"
        self.anomalies: list[dict[str, Any]] = []
        self.reports: list[ImageReport] = []
        self.verses: list[VerseRecord] = []

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.columns_dir.mkdir(parents=True, exist_ok=True)

        processed = success = warnings = 0

        for image_name in TARGET_IMAGES:
            processed += 1
            report = self.process_image(self.images_dir / image_name)
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

        image = self.Image.open(image_path).convert("L")
        tokens = self._ocr_tokens(image)
        if not tokens:
            obs = "OCR sin tokens detectables."
            self._warn(image_path.name, obs)
            return ImageReport(file_name=image_path.name, status="warning", observations=[obs])

        split_x, has_2_cols = self._detect_column_split(tokens, image.width)
        columns = self._tokens_to_columns(tokens, split_x, has_2_cols)

        report = ImageReport(file_name=image_path.name, status="ok", columns_detected=2 if has_2_cols else 1)
        if not has_2_cols:
            report.status = "warning"
            obs = "No se detectaron dos columnas con suficiente confianza."
            report.observations.append(obs)
            self._warn(image_path.name, obs)

        all_lines: list[str] = []
        for idx, col_tokens in enumerate(columns, start=1):
            body_tokens, footnote_obs = self._drop_footnote_tokens(col_tokens)
            if footnote_obs:
                report.observations.append(footnote_obs)

            col_lines = self._tokens_to_lines(body_tokens)
            self._save_column_text(image_path.name, idx, col_lines)
            all_lines.extend(col_lines)

        book, chapter = self._extract_metadata(all_lines)
        report.book, report.chapter = book, chapter

        if book is None:
            obs = "Libro no detectado en cabecera."
            report.observations.append(obs)
            self._warn(image_path.name, obs)
        if chapter is None:
            obs = "Capítulo no detectado en cabecera."
            report.observations.append(obs)
            self._warn(image_path.name, obs)

        verses, v_obs = self._extract_verses(all_lines, image_path.name, book, chapter)
        self.verses.extend(verses)
        report.observations.extend(v_obs)

        if not verses:
            report.status = "warning"
            obs = "No se detectaron versículos válidos."
            report.observations.append(obs)
            self._warn(image_path.name, obs)

        return report

    def _ocr_tokens(self, image: Any) -> list[OcrToken]:
        enhanced = self.ImageOps.autocontrast(image)
        data = self.pytesseract.image_to_data(
            enhanced,
            lang="spa",
            config="--oem 1 --psm 3",
            output_type=self.pytesseract.Output.DICT,
        )
        tokens: list[OcrToken] = []
        for i, text in enumerate(data["text"]):
            cleaned = text.strip()
            if not cleaned:
                continue
            conf = float(data["conf"][i]) if data["conf"][i] not in ("-1", -1) else -1.0
            if conf < 25:
                continue
            tokens.append(
                OcrToken(
                    text=cleaned,
                    left=int(data["left"][i]),
                    top=int(data["top"][i]),
                    width=int(data["width"][i]),
                    height=int(data["height"][i]),
                    conf=conf,
                )
            )
        return tokens

    def _detect_column_split(self, tokens: list[OcrToken], image_width: int) -> tuple[int, bool]:
        centers = [t.left + t.width // 2 for t in tokens]
        if len(centers) < 20:
            return image_width // 2, False

        bins = 40
        bin_w = max(1, image_width // bins)
        hist = [0 for _ in range(bins)]
        for c in centers:
            idx = min(bins - 1, max(0, c // bin_w))
            hist[idx] += 1

        central_start = int(0.35 * bins)
        central_end = int(0.65 * bins)
        center_hist = hist[central_start:central_end]
        if not center_hist:
            return image_width // 2, False

        valley_rel = min(range(len(center_hist)), key=lambda i: center_hist[i])
        valley_bin = central_start + valley_rel
        split_x = int((valley_bin + 0.5) * bin_w)

        left_count = sum(1 for c in centers if c < split_x)
        right_count = sum(1 for c in centers if c >= split_x)
        balanced = min(left_count, right_count) / max(left_count, right_count) if max(left_count, right_count) else 0
        has_2_cols = balanced > 0.35
        return split_x, has_2_cols

    def _tokens_to_columns(self, tokens: list[OcrToken], split_x: int, has_2_cols: bool) -> list[list[OcrToken]]:
        if not has_2_cols:
            return [sorted(tokens, key=lambda t: (t.top, t.left))]

        left_col = [t for t in tokens if (t.left + t.width // 2) < split_x]
        right_col = [t for t in tokens if (t.left + t.width // 2) >= split_x]

        # Evita mezcla: descarta outliers extremos por columna
        left_col = sorted(left_col, key=lambda t: (t.top, t.left))
        right_col = sorted(right_col, key=lambda t: (t.top, t.left))
        return [left_col, right_col]

    def _drop_footnote_tokens(self, tokens: list[OcrToken]) -> tuple[list[OcrToken], str | None]:
        if len(tokens) < 12:
            return tokens, None

        tops = sorted([t.top for t in tokens])
        heights = [max(1, t.height) for t in tokens]
        median_h = median(heights)
        cutoff_y = tops[int(len(tops) * 0.84)]

        kept: list[OcrToken] = []
        dropped = 0
        for t in tokens:
            tiny = t.height < (0.82 * median_h)
            at_bottom = t.top >= cutoff_y
            fn_marker = bool(re.match(r"^\d+[\)\.]$", t.text))
            if at_bottom and (tiny or fn_marker):
                dropped += 1
                continue
            kept.append(t)

        if dropped > 8:
            msg = f"Bloque de notas al pie filtrado ({dropped} tokens descartados)."
            return kept, msg
        return kept, None

    def _tokens_to_lines(self, tokens: list[OcrToken]) -> list[str]:
        if not tokens:
            return []

        tokens_sorted = sorted(tokens, key=lambda t: (t.top, t.left))
        lines: list[list[OcrToken]] = []

        for token in tokens_sorted:
            if not lines:
                lines.append([token])
                continue
            current = lines[-1]
            y_ref = median([w.top for w in current])
            h_ref = max(10, int(median([w.height for w in current])))
            if abs(token.top - y_ref) <= int(0.75 * h_ref):
                current.append(token)
            else:
                lines.append([token])

        rendered: list[str] = []
        for group in lines:
            text = " ".join(tok.text for tok in sorted(group, key=lambda t: t.left)).strip()
            if text:
                rendered.append(text)
        return rendered

    def _extract_metadata(self, lines: list[str]) -> tuple[str | None, str | None]:
        header_window = "\n".join(lines[:80]).upper()
        book_match = BOOK_RE.search(header_window)
        chapter_match = CHAPTER_RE.search(header_window)

        book: str | None = None
        if book_match:
            raw = self._normalize_token(book_match.group(1))
            for known, canonical in KNOWN_BOOKS.items():
                if known in raw:
                    book = canonical
                    break
            if book is None:
                book = book_match.group(1).strip().title()

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
            line = self._clean_line(raw_line)
            if not line:
                continue

            # omite rótulos editoriales antes del primer versículo
            if not started and EDITORIAL_RE.match(line):
                continue

            match = VERSE_START_RE.match(line)
            if match:
                num = int(match.group(1))
                if current_num is not None and num <= current_num:
                    observations.append(f"Secuencia de versículos no creciente detectada ({current_num}->{num}).")

                self._flush_verse(verses, current_num, current_parts, book, chapter, image_name)
                current_num = num
                started = True
                first = (match.group(2) or "").strip()
                current_parts = [first] if first else []
                continue

            if not started:
                continue

            if self._looks_like_noise_or_note(line):
                observations.append("Línea con patrón de nota/ruido omitida dentro de bloque bíblico.")
                continue

            if current_num is not None:
                current_parts.append(line)

        self._flush_verse(verses, current_num, current_parts, book, chapter, image_name)

        for verse in verses:
            if len(verse.texto) > 420:
                obs = f"Versículo {verse.versiculo} demasiado largo; posible mezcla de zonas."
                observations.append(obs)
                self._warn(image_name, obs)

        return verses, list(dict.fromkeys(observations))

    def _flush_verse(
        self,
        verses: list[VerseRecord],
        num: int | None,
        parts: list[str],
        book: str | None,
        chapter: str | None,
        image_name: str,
    ) -> None:
        if num is None:
            return
        text = " ".join(p for p in parts if p).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) < 4:
            self._warn(image_name, f"Versículo {num} descartado por longitud mínima.")
            return
        verses.append(VerseRecord(libro=book, capitulo=chapter, versiculo=num, texto=text, imagen_origen=image_name))

    def _looks_like_noise_or_note(self, line: str) -> bool:
        if re.match(r"^\d+[\)\.]\s+[a-záéíóúñ]", line):
            return True
        if re.match(r"^[\*†‡]+", line):
            return True
        if len(line) < 6 and not re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]", line):
            return True
        return False

    def _clean_line(self, line: str) -> str:
        line = re.sub(r"\s+", " ", line).strip()
        line = line.replace("|", "")
        return line

    def _normalize_token(self, value: str) -> str:
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = re.sub(r"[^A-Z\s]", "", value.upper())
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _save_column_text(self, image_name: str, column_index: int, lines: list[str]) -> None:
        out = self.columns_dir / f"{Path(image_name).stem}_col{column_index}.txt"
        out.write_text("\n".join(lines), encoding="utf-8")

    def _warn(self, image_name: str, message: str) -> None:
        self.anomalies.append({"imagen": image_name, "warning": message})

    def _write_outputs(self) -> None:
        (self.output_dir / "image_report.json").write_text(
            json.dumps([asdict(r) for r in self.reports], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.output_dir / "verses.json").write_text(
            json.dumps([asdict(v) for v in self.verses], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.output_dir / "anomalies.json").write_text(
            json.dumps(self.anomalies, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _print_summary(self, processed: int, success: int, warnings: int) -> None:
        print("=== Extractor v0 - Resumen de ejecución ===")
        print(f"Imágenes procesadas: {processed}")
        print(f"Imágenes exitosas: {success}")
        print(f"Imágenes con advertencias: {warnings}\n")
        print("=== Reporte por imagen ===")
        for report in self.reports:
            print(f"- Archivo: {report.file_name}")
            print(f"  Libro: {report.book or 'N/D'}")
            print(f"  Capítulo: {report.chapter or 'N/D'}")
            print(f"  Columnas detectadas: {report.columns_detected}")
            print(f"  Observaciones: {' | '.join(sorted(set(report.observations))) if report.observations else 'ninguna'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extractor v0 Torres Amat (4 imágenes)")
    parser.add_argument("--images-dir", default="AI156_images", help="Carpeta con imágenes de entrada")
    parser.add_argument("--output-dir", default="output_v0", help="Carpeta de salida")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        TorresAmatExtractorV0(Path(args.images_dir), Path(args.output_dir)).run()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
