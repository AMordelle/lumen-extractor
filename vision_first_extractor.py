#!/usr/bin/env python3
"""Extractor experimental vision-first para páginas bíblicas escaneadas."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
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
class Region:
    label: str
    bbox: Tuple[int, int, int, int]
    score: float


@dataclass
class PageResult:
    imagen_origen: str
    libro: Optional[str]
    capitulo: Optional[int]
    tipo_pagina: str
    columnas_biblicas: int
    zonas_detectadas: Dict[str, bool]
    versiculos_visibles: List[int]


class VisionFirstExtractor:
    def __init__(self, debug: bool = True) -> None:
        self.debug = debug

    def run_on_images(self, image_paths: Sequence[Path], output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        pages_out = output_dir / "pages_json"
        verses_out = output_dir / "verses_json"
        ann_out = output_dir / "annotated"
        crops_out = output_dir / "crops"
        for folder in (pages_out, verses_out, ann_out, crops_out):
            folder.mkdir(parents=True, exist_ok=True)

        for image_path in image_paths:
            page = self.process_page(image_path, ann_out, crops_out)
            (pages_out / f"{image_path.stem}.page.json").write_text(
                json.dumps(asdict(page["page"]), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (verses_out / f"{image_path.stem}.verses.json").write_text(
                json.dumps(page["verses"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def process_page(self, image_path: Path, ann_out: Path, crops_out: Path) -> Dict[str, object]:
        image = cv2.imread(str(image_path))
        if image is None:
            raise FileNotFoundError(f"No se pudo abrir la imagen: {image_path}")
        h, w = image.shape[:2]

        layout = self.detect_layout_regions(image)
        text_by_zone = {r.label: self.ocr_region(image, r.bbox) for r in layout}

        libro = self.infer_book(text_by_zone)
        capitulo = self.infer_chapter(text_by_zone)
        columnas = self.infer_columns(image)
        verses = self.extract_verses(image, layout, libro, capitulo, image_path.name)
        zone_flags = {
            "titulo_libro": any(r.label == "titulo_libro" for r in layout),
            "titulo_capitulo": any(r.label == "titulo_capitulo" for r in layout),
            "resumen_capitulo": any(r.label == "resumen_capitulo" for r in layout),
            "cuerpo_biblico": any(r.label == "cuerpo_biblico" for r in layout),
            "notas_al_pie": any(r.label == "notas_al_pie" for r in layout),
            "ornamento_central": any(r.label == "ornamento_central" for r in layout),
        }

        if zone_flags["titulo_libro"] and zone_flags["cuerpo_biblico"]:
            tipo_pagina = "inicio_libro_con_texto_biblico"
        elif zone_flags["cuerpo_biblico"]:
            tipo_pagina = "texto_biblico"
        else:
            tipo_pagina = "mixta_editorial"

        page_json = PageResult(
            imagen_origen=image_path.name,
            libro=libro,
            capitulo=capitulo,
            tipo_pagina=tipo_pagina,
            columnas_biblicas=columnas,
            zonas_detectadas=zone_flags,
            versiculos_visibles=sorted({v["versiculo"] for v in verses if v.get("versiculo") is not None}),
        )

        self.save_debug(image, image_path.stem, layout, ann_out, crops_out)

        return {"page": page_json, "verses": verses}

    def detect_layout_regions(self, image: np.ndarray) -> List[Region]:
        h, w = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        thr = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 15
        )

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (19, 11))
        merged = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, kernel, iterations=1)
        contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        blocks: List[Tuple[int, int, int, int]] = []
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            area = bw * bh
            if area < (w * h) * 0.005:
                continue
            if bh < 30 or bw < 60:
                continue
            blocks.append((x, y, bw, bh))

        blocks.sort(key=lambda b: b[1])
        regions: List[Region] = []

        for x, y, bw, bh in blocks:
            text = self.ocr_region(image, (x, y, bw, bh), psm=6)
            uc = text.upper()
            label = "cuerpo_biblico"
            score = 0.5

            if y > int(h * 0.82):
                label, score = "notas_al_pie", 0.9
            elif y < int(h * 0.20) and bw > int(w * 0.45):
                if any(k in uc for k in BOOK_HINTS):
                    label, score = "titulo_libro", 0.95
                elif "CAPITULO" in uc or "CAPÍTULO" in uc:
                    label, score = "titulo_capitulo", 0.95
                else:
                    label, score = "resumen_capitulo", 0.65
            elif int(h * 0.20) <= y <= int(h * 0.55) and bw > int(w * 0.6) and len(text.split()) > 20:
                label, score = "resumen_capitulo", 0.7
            elif self.looks_ornament(image, (x, y, bw, bh)):
                label, score = "ornamento_central", 0.85

            regions.append(Region(label=label, bbox=(x, y, bw, bh), score=score))

        if not any(r.label == "ornamento_central" for r in regions):
            cx, cy = int(w * 0.5), int(h * 0.58)
            ow, oh = int(w * 0.10), int(h * 0.08)
            regions.append(Region("ornamento_central", (cx - ow // 2, cy - oh // 2, ow, oh), 0.35))

        return self.deduplicate_regions(regions)

    def deduplicate_regions(self, regions: List[Region]) -> List[Region]:
        best: Dict[str, Region] = {}
        for r in regions:
            prior = best.get(r.label)
            if prior is None or (r.score > prior.score and r.bbox[2] * r.bbox[3] > prior.bbox[2] * prior.bbox[3] * 0.5):
                best[r.label] = r
            elif r.label == "cuerpo_biblico" and prior and prior.label == "cuerpo_biblico":
                # conservar múltiples cuerpos bíblicos (columnas)
                pass
        bodies = [r for r in regions if r.label == "cuerpo_biblico"]
        others = [v for k, v in best.items() if k != "cuerpo_biblico"]
        return others + bodies

    def looks_ornament(self, image: np.ndarray, bbox: Tuple[int, int, int, int]) -> bool:
        x, y, bw, bh = bbox
        crop = image[y : y + bh, x : x + bw]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 40, 120)
        edge_ratio = float(np.count_nonzero(edges)) / float(edges.size)
        txt = self.ocr_region(image, bbox, psm=10)
        return edge_ratio > 0.12 and len(txt.strip()) < 5 and 0.4 < (bw / max(1, bh)) < 2.5

    def infer_book(self, text_by_zone: Dict[str, str]) -> Optional[str]:
        text = " ".join(text_by_zone.values()).upper()
        for hint, normalized in BOOK_HINTS.items():
            if hint in text:
                return normalized
        return None

    def infer_chapter(self, text_by_zone: Dict[str, str]) -> Optional[int]:
        text = " ".join(text_by_zone.values()).upper()
        m = re.search(r"CAP[ÍI]TULO\s+([0-9]{1,3})", text)
        if m:
            return int(m.group(1))
        isolated = re.findall(r"\b([0-9]{1,3})\b", text)
        if isolated:
            return int(isolated[0])
        return None

    def infer_columns(self, image: np.ndarray) -> int:
        h, w = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        body = gray[int(h * 0.20) : int(h * 0.82), :]
        thr = cv2.threshold(body, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        projection = np.sum(thr > 0, axis=0)
        projection = cv2.GaussianBlur(projection.astype(np.float32), (0, 0), sigmaX=7)
        midpoint = w // 2
        valley = np.argmin(projection[int(w * 0.35) : int(w * 0.65)]) + int(w * 0.35)
        if projection[valley] < np.percentile(projection, 20) and abs(valley - midpoint) < int(w * 0.12):
            return 2
        return 1

    def extract_verses(
        self,
        image: np.ndarray,
        layout: List[Region],
        libro: Optional[str],
        capitulo: Optional[int],
        imagen: str,
    ) -> List[Dict[str, object]]:
        bodies = [r for r in layout if r.label == "cuerpo_biblico"]
        if not bodies:
            return []

        verses: List[Dict[str, object]] = []
        for region in bodies:
            x, y, bw, bh = region.bbox
            crop = image[y : y + bh, x : x + bw]
            data = pytesseract.image_to_data(crop, output_type=pytesseract.Output.DICT, config="--oem 3 --psm 6")
            current_verse = None
            current_text: List[str] = []
            for token, conf in zip(data["text"], data["conf"]):
                tok = token.strip()
                if not tok or int(float(conf)) < 35:
                    continue
                if re.fullmatch(r"\d{1,3}", tok):
                    if current_verse is not None and current_text:
                        verses.append(
                            {
                                "imagen_origen": imagen,
                                "libro": libro,
                                "capitulo": capitulo,
                                "versiculo": current_verse,
                                "texto": " ".join(current_text).strip(),
                                "bbox_origen": [x, y, bw, bh],
                            }
                        )
                    current_verse = int(tok)
                    current_text = []
                elif current_verse is not None:
                    current_text.append(tok)
            if current_verse is not None and current_text:
                verses.append(
                    {
                        "imagen_origen": imagen,
                        "libro": libro,
                        "capitulo": capitulo,
                        "versiculo": current_verse,
                        "texto": " ".join(current_text).strip(),
                        "bbox_origen": [x, y, bw, bh],
                    }
                )
        return verses

    def save_debug(
        self,
        image: np.ndarray,
        stem: str,
        layout: List[Region],
        ann_out: Path,
        crops_out: Path,
    ) -> None:
        annotated = image.copy()
        colors = {
            "titulo_libro": (255, 0, 0),
            "titulo_capitulo": (0, 0, 255),
            "resumen_capitulo": (0, 128, 255),
            "cuerpo_biblico": (0, 255, 0),
            "notas_al_pie": (255, 0, 255),
            "ornamento_central": (0, 255, 255),
        }
        for idx, region in enumerate(layout):
            x, y, w, h = region.bbox
            c = colors.get(region.label, (180, 180, 180))
            cv2.rectangle(annotated, (x, y), (x + w, y + h), c, 3)
            cv2.putText(
                annotated,
                f"{region.label}:{region.score:.2f}",
                (x, max(30, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                c,
                2,
                cv2.LINE_AA,
            )
            crop = image[y : y + h, x : x + w]
            cv2.imwrite(str(crops_out / f"{stem}.{idx:02d}.{region.label}.jpg"), crop)

        cv2.imwrite(str(ann_out / f"{stem}.annotated.jpg"), annotated)

    def ocr_region(self, image: np.ndarray, bbox: Tuple[int, int, int, int], psm: int = 4) -> str:
        x, y, w, h = bbox
        crop = image[y : y + h, x : x + w]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        proc = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        config = f"--oem 3 --psm {psm}"
        return pytesseract.image_to_string(proc, lang="spa", config=config)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extractor vision-first para Biblia Torres Amat")
    parser.add_argument("--input-dir", type=Path, default=Path("AI156_images"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/vision_first"))
    parser.add_argument(
        "--images",
        nargs="*",
        default=["AI156_0018.jpg", "AI156_0020.jpg", "AI156_0074.jpg", "AI156_0257.jpg"],
        help="Lista de imágenes objetivo dentro de --input-dir",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_paths = [args.input_dir / name for name in args.images]
    missing = [str(p) for p in image_paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Faltan imágenes requeridas para la corrida vision-first:\n- " + "\n- ".join(missing)
        )

    extractor = VisionFirstExtractor(debug=True)
    extractor.run_on_images(image_paths=image_paths, output_dir=args.output_dir)
    print(f"Proceso completado. Salidas en: {args.output_dir}")


if __name__ == "__main__":
    main()
