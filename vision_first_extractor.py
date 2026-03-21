#!/usr/bin/env python3
"""Extractor vision-first v2 para transcripción literal de versículos (Torres Amat)."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
import pytesseract

BOOK_HINTS = {
    "GENESIS": "Génesis",
    "GENÉSIS": "Génesis",
    "EXODO": "Éxodo",
    "ÉXODO": "Éxodo",
    "LEVITICO": "Levítico",
    "LEVÍTICO": "Levítico",
    "NUMEROS": "Números",
    "NÚMEROS": "Números",
    "DEUTERONOMIO": "Deuteronomio",
    "DEUTERONÓMIO": "Deuteronomio",
}


@dataclass
class Zone:
    label: str
    bbox: Tuple[int, int, int, int]


def _clean_token(token: str) -> str:
    return token.strip().replace("\n", " ")


class VisionFirstExtractorV2:
    """Analiza layout visual primero y transcribe versículos en orden de columnas."""

    def __init__(self, tesseract_lang: str = "spa") -> None:
        self.tesseract_lang = tesseract_lang

    def run(self, image_paths: Sequence[Path], output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        debug_ann = output_dir / "annotated"
        debug_crops = output_dir / "crops"
        pages_json = output_dir / "pages_json"
        verses_json = output_dir / "verses_json"
        for folder in (debug_ann, debug_crops, pages_json, verses_json):
            folder.mkdir(parents=True, exist_ok=True)

        for image_path in image_paths:
            image = cv2.imread(str(image_path))
            if image is None:
                raise FileNotFoundError(f"No se pudo abrir {image_path}")

            zones = self.detect_zones(image)
            book, chapter = self.infer_book_and_chapter(image, zones)
            verses = self.transcribe_verses(image, zones, image_path.name, book, chapter)

            page_payload = {
                "imagen_origen": image_path.name,
                "libro": book,
                "capitulo": chapter,
                "zonas": {z.label: True for z in zones},
                "columnas_biblicas": len([z for z in zones if z.label == "cuerpo_biblico"]),
                "versiculos_detectados": [v["versiculo"] for v in verses if v.get("versiculo") is not None],
            }

            (pages_json / f"{image_path.stem}.page.json").write_text(
                json.dumps(page_payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (verses_json / f"{image_path.stem}.verses.json").write_text(
                json.dumps(verses, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            self.save_debug(image, zones, image_path.stem, debug_ann, debug_crops)

    def detect_zones(self, image: np.ndarray) -> List[Zone]:
        h, w = image.shape[:2]
        zones: List[Zone] = []

        # Estructura macro visual: parte superior (títulos), cuerpo en 2 columnas, pie.
        top_h = int(h * 0.17)
        foot_h = int(h * 0.16)
        body_top = top_h
        body_bottom = h - foot_h

        top_zone = Zone("encabezado", (0, 0, w, top_h))
        foot_zone = Zone("notas_al_pie", (0, body_bottom, w, foot_h))
        zones.extend([top_zone, foot_zone])

        # Detección de canal central (gutter + ornamento) via proyección vertical.
        gray = cv2.cvtColor(image[body_top:body_bottom, :], cv2.COLOR_BGR2GRAY)
        thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        proj = np.sum(thr > 0, axis=0).astype(np.float32)
        proj = cv2.GaussianBlur(proj, (0, 0), sigmaX=9)

        center_min = int(w * 0.35)
        center_max = int(w * 0.65)
        valley = int(np.argmin(proj[center_min:center_max]) + center_min)
        gutter_w = max(int(w * 0.06), 30)

        left_x0, left_x1 = 0, max(valley - gutter_w // 2, int(w * 0.42))
        right_x0, right_x1 = min(valley + gutter_w // 2, int(w * 0.58)), w

        zones.append(Zone("cuerpo_biblico", (left_x0, body_top, left_x1 - left_x0, body_bottom - body_top)))
        zones.append(Zone("cuerpo_biblico", (right_x0, body_top, right_x1 - right_x0, body_bottom - body_top)))

        orn_h = int((body_bottom - body_top) * 0.10)
        orn_y = body_top + int((body_bottom - body_top) * 0.50)
        zones.append(Zone("ornamento_central", (max(0, valley - gutter_w), orn_y, gutter_w * 2, orn_h)))

        return zones

    def infer_book_and_chapter(self, image: np.ndarray, zones: List[Zone]) -> Tuple[Optional[str], Optional[int]]:
        header = next((z for z in zones if z.label == "encabezado"), None)
        if header is None:
            return None, None
        text = self.ocr_bbox(image, header.bbox, psm=6).upper()

        book: Optional[str] = None
        for hint, canonical in BOOK_HINTS.items():
            if hint in text:
                book = canonical
                break

        chapter: Optional[int] = None
        m = re.search(r"CAP[ÍI]TULO\s*([0-9]{1,3})", text)
        if m:
            chapter = int(m.group(1))

        return book, chapter

    def transcribe_verses(
        self,
        image: np.ndarray,
        zones: List[Zone],
        image_name: str,
        book: Optional[str],
        chapter: Optional[int],
    ) -> List[Dict[str, object]]:
        body_zones = [z for z in zones if z.label == "cuerpo_biblico"]
        body_zones.sort(key=lambda z: z.bbox[0])  # izquierda -> derecha

        raw_verses: List[Dict[str, object]] = []
        for zone in body_zones:
            raw_verses.extend(self.transcribe_zone(image, zone, image_name, book, chapter))

        # Deduplicación y orden numérico estable según aparición.
        ordered: List[Dict[str, object]] = []
        seen = set()
        for item in raw_verses:
            number = item.get("versiculo")
            key = (number, item.get("texto"))
            if number is None or key in seen:
                continue
            seen.add(key)
            ordered.append(item)

        ordered.sort(key=lambda x: x["versiculo"])
        return ordered

    def transcribe_zone(
        self,
        image: np.ndarray,
        zone: Zone,
        image_name: str,
        book: Optional[str],
        chapter: Optional[int],
    ) -> List[Dict[str, object]]:
        x, y, w, h = zone.bbox
        crop = image[y : y + h, x : x + w]
        data = pytesseract.image_to_data(
            crop,
            lang=self.tesseract_lang,
            config="--oem 3 --psm 6",
            output_type=pytesseract.Output.DICT,
        )

        lines: Dict[Tuple[int, int, int], List[str]] = {}
        for i in range(len(data["text"])):
            token = _clean_token(data["text"][i])
            if not token:
                continue
            try:
                conf = float(data["conf"][i])
            except ValueError:
                conf = -1
            if conf < 30:
                continue
            key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
            lines.setdefault(key, []).append(token)

        ordered_lines = [" ".join(lines[k]).strip() for k in sorted(lines.keys()) if lines[k]]

        verses: List[Dict[str, object]] = []
        current_number: Optional[int] = None
        current_parts: List[str] = []

        for line in ordered_lines:
            # descartar líneas editoriales/no bíblicas frecuentes en cuerpo
            if re.search(r"\bNOTA\b|\bCAPITULO\b|\bCAPÍTULO\b", line.upper()):
                continue

            m = re.match(r"^(\d{1,3})\s+(.*)$", line)
            if m:
                if current_number is not None and current_parts:
                    verses.append(
                        {
                            "imagen_origen": image_name,
                            "libro": book,
                            "capitulo": chapter,
                            "versiculo": current_number,
                            "texto": " ".join(current_parts).strip(),
                        }
                    )
                current_number = int(m.group(1))
                current_parts = [m.group(2).strip()] if m.group(2).strip() else []
            else:
                if current_number is not None:
                    current_parts.append(line)

        if current_number is not None and current_parts:
            verses.append(
                {
                    "imagen_origen": image_name,
                    "libro": book,
                    "capitulo": chapter,
                    "versiculo": current_number,
                    "texto": " ".join(current_parts).strip(),
                }
            )

        return verses

    def save_debug(self, image: np.ndarray, zones: List[Zone], stem: str, ann_dir: Path, crops_dir: Path) -> None:
        canvas = image.copy()
        colors = {
            "encabezado": (255, 0, 0),
            "cuerpo_biblico": (0, 255, 0),
            "ornamento_central": (0, 255, 255),
            "notas_al_pie": (255, 0, 255),
        }

        for i, zone in enumerate(zones):
            x, y, w, h = zone.bbox
            color = colors.get(zone.label, (200, 200, 200))
            cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 2)
            cv2.putText(canvas, zone.label, (x + 5, max(20, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            cv2.imwrite(str(crops_dir / f"{stem}.{i:02d}.{zone.label}.jpg"), image[y : y + h, x : x + w])

        cv2.imwrite(str(ann_dir / f"{stem}.annotated.jpg"), canvas)

    def ocr_bbox(self, image: np.ndarray, bbox: Tuple[int, int, int, int], psm: int) -> str:
        x, y, w, h = bbox
        crop = image[y : y + h, x : x + w]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        return pytesseract.image_to_string(thr, lang=self.tesseract_lang, config=f"--oem 3 --psm {psm}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extractor vision-first v2 (transcripción literal)")
    parser.add_argument("--input-dir", type=Path, default=Path("AI156_images"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/vision_first_v2"))
    parser.add_argument(
        "--images",
        nargs="*",
        default=["AI156_0018.jpg", "AI156_0020.jpg", "AI156_0074.jpg", "AI156_0257.jpg"],
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_paths = [args.input_dir / name for name in args.images]
    missing = [str(p) for p in image_paths if not p.exists()]
    if missing:
        raise FileNotFoundError("Faltan imágenes:\n- " + "\n- ".join(missing))

    VisionFirstExtractorV2().run(image_paths=image_paths, output_dir=args.output_dir)
    print(f"OK: salida en {args.output_dir}")


if __name__ == "__main__":
    main()
