"""Prétraitement local des plans cadastraux (OpenCV) — sans jamais inventer de coordonnées GPS.

Intégré depuis la branche master. Le contour détecté est exprimé en PIXELS du dessin : il ne devient
une parcelle géographique qu'après placement et vérification par une personne (échelle, position,
orientation), dans la page « Mes terres agricoles ».
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

MAX_SIDE_PX = 4000          # au-delà, on réduit l'image (mémoire, temps de calcul)
PDF_TARGET_SIDE_PX = 2400   # rendu de la 1re page d'un PDF


def _limit_size(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    scale = MAX_SIDE_PX / max(height, width)
    if scale < 1.0:
        image = cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    return image


def load_document(path: Path) -> np.ndarray:
    """Charge une image ou la première page d'un PDF, en BGR."""
    if path.suffix.lower() == ".pdf":
        import fitz  # PyMuPDF, importé ici : seul le traitement des PDF en a besoin

        with fitz.open(path) as document:
            if document.page_count == 0:
                raise ValueError("PDF sans page")
            page = document.load_page(0)
            longest = max(page.rect.width, page.rect.height) or 1.0
            zoom = max(0.5, min(4.0, PDF_TARGET_SIDE_PX / longest))
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3)
            return _limit_size(cv2.cvtColor(image, cv2.COLOR_RGB2BGR))

    data = np.fromfile(str(path), dtype=np.uint8)       # fromfile : chemins Windows non ASCII
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Impossible de lire le document (image illisible)")
    return _limit_size(image)


def preprocess(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Niveaux de gris, contraste local (CLAHE), réduction du bruit, seuillage adaptatif."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blurred = cv2.bilateralFilter(enhanced, 7, 50, 50)
    threshold = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, 8)
    return enhanced, threshold


MAX_CANDIDATES = 5


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter or 1)


def extract_drawing(image: np.ndarray) -> dict[str, Any]:
    """Contours plausibles d'une parcelle, en pixels du dessin, du plus probable au moins probable.

    Un vrai plan cadastral contient un cadre de page, un cartouche (titre) et des parcelles voisines :
    on écarte le cadre et ce qui touche le bord, on fusionne les doublons (un trait épais donne un
    contour intérieur et un extérieur), puis on propose jusqu'à 5 candidats ; la personne choisit.
    """
    _enhanced, threshold = preprocess(image)
    height, width = image.shape[:2]

    # Le texte (cotes, numéros de bornes, surface) coupe l'intérieur des parcelles : on retire les petits
    # éléments sombres isolés (lettres, chiffres) avant de chercher les contours. Les traits des parcelles,
    # longs et connectés, restent.
    dark = cv2.bitwise_not(threshold)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    limit = 0.04 * float(np.hypot(width, height))
    small = (stats[:, cv2.CC_STAT_WIDTH] < limit) & (stats[:, cv2.CC_STAT_HEIGHT] < limit)
    small[0] = False                      # composante 0 = le fond
    dark[small[labels]] = 0
    threshold = cv2.bitwise_not(dark)
    # RETR_CCOMP : on ne garde que le bord EXTÉRIEUR de chaque zone blanche (l'intérieur d'une parcelle
    # fermée). Les « trous » renvoyés par RETR_LIST sont le pourtour d'un groupe de traits noirs
    # (plusieurs parcelles accolées) : jamais une parcelle.
    found, hierarchy = cv2.findContours(threshold, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    contours = list(found) if hierarchy is None else [c for c, h in zip(found, hierarchy[0]) if h[3] == -1]

    page = float(width * height)
    margin = 0.01 * max(width, height)
    kept: list[tuple[list[list[int]], tuple[int, int, int, int], float]] = []
    frames: list[tuple[list[list[int]], tuple[int, int, int, int], float]] = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:60]:
        area = float(cv2.contourArea(contour))
        if area < page * 0.01 or area > page * 0.98:
            continue                      # trop petit, ou la feuille entière
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.01 * peri, True)
        if len(approx) < 3:
            continue
        box = cv2.boundingRect(approx)
        x, y, w, h = box
        points = [[int(p[0][0]), int(p[0][1])] for p in approx]
        if any(_iou(box, other[1]) > 0.9 for other in kept + frames):
            continue                      # bord intérieur/extérieur du même trait
        touches_edge = x <= margin or y <= margin or x + w >= width - margin or y + h >= height - margin
        page_frame = len(approx) == 4 and area / float(w * h) > 0.95 and w * h > page * 0.5
        # « Solidité » = surface / surface de l'enveloppe convexe. Une parcelle est compacte (> 0,7 même en L) ;
        # l'espace blanc résiduel entre plusieurs parcelles est tortueux. Score = surface × solidité².
        hull = float(cv2.contourArea(cv2.convexHull(contour))) or 1.0
        score = area * (area / hull) ** 2
        (frames if touches_edge or page_frame else kept).append((points, box, score))

    kept.sort(key=lambda c: c[2], reverse=True)
    frames.sort(key=lambda c: c[2], reverse=True)
    ordered = kept or frames              # à défaut, on montre quand même le cadre (la personne jugera)
    base = {"document_type": "plan_parcellaire", "coordinate_reference_system": None,
            "image_width": int(width), "image_height": int(height)}
    if not ordered:
        return {**base, "boundary_points": [], "candidates": [], "detected": False, "extraction_confidence": 0.0}

    candidates = []
    for points, _box, _area in ordered[:MAX_CANDIDATES]:
        confidence = min(0.95, 0.55 + min(len(points), 10) * 0.025) * (1.0 if kept else 0.5)
        candidates.append({
            "boundary_points": [{"id": _label(i), "x": x, "y": y} for i, (x, y) in enumerate(points)],
            "extraction_confidence": round(confidence, 2),
        })
    return {**base, "boundary_points": candidates[0]["boundary_points"], "candidates": candidates,
            "detected": True, "extraction_confidence": candidates[0]["extraction_confidence"]}


def _label(index: int) -> str:
    """A, B, …, Z, A1, B1, … (noms des bornes comme sur un plan)."""
    return chr(65 + index % 26) + (str(index // 26) if index >= 26 else "")


def extract_text(path: Path) -> dict[str, Any]:
    """Tente PaddleOCR s'il est installé ; sinon renvoie un texte vide (le contour reste utilisable)."""
    try:
        from paddleocr import PaddleOCR
    except Exception:
        return {"text": [], "ocr_confidence": 0.0, "ocr_status": "engine_unavailable"}
    try:
        ocr = PaddleOCR(use_angle_cls=True, lang="fr", show_log=False)
        result = ocr.ocr(str(path), cls=True)
        texts = [line[1][0] for page in (result or []) for line in (page or [])]
        scores = [float(line[1][1]) for page in (result or []) for line in (page or [])]
        return {"text": texts, "ocr_confidence": round(sum(scores) / len(scores), 3) if scores else 0.0,
                "ocr_status": "ok"}
    except Exception:
        return {"text": [], "ocr_confidence": 0.0, "ocr_status": "engine_error"}
