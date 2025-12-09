# services/face_service.py
from __future__ import annotations
import pickle
import threading
from typing import List, Tuple, Optional

import numpy as np
import cv2

try:
    from deepface import DeepFace
    _HAVE_DF = True
except Exception:
    DeepFace = None
    _HAVE_DF = False

from db.db_conn import get_conn

MODEL_NAME = "Facenet512"
THRESHOLD = 0.30
DETECTOR_BACKEND = "opencv"

# ===== DB helpers =====
def _to_blob(vec: np.ndarray) -> bytes:
    return pickle.dumps(vec.astype(np.float32), protocol=pickle.HIGHEST_PROTOCOL)

def _from_blob(b: bytes) -> np.ndarray:
    return pickle.loads(b)

def enroll_embedding(name: str, emb: np.ndarray) -> int:
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("INSERT INTO face_data(name, encoding) VALUES (%s, %s)", (name, _to_blob(emb)))
        cn.commit()
        return cur.lastrowid

def list_embeddings() -> List[Tuple[int, str, np.ndarray]]:
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("SELECT id, name, encoding FROM face_data")
        rows = cur.fetchall() or []
    out: List[Tuple[int, str, np.ndarray]] = []
    for rid, name, blob in rows:
        try:
            out.append((rid, name or f"face_{rid}", _from_blob(blob)))
        except Exception:
            pass
    return out

def delete_embeddings_by_name(name: str) -> int:
    """
    Xoá tất cả embedding trong bảng face_data có cùng name.
    Trả về số bản ghi đã xoá.
    """
    if not name:
        return 0
    with get_conn() as cn, cn.cursor() as cur:
        cur.execute("DELETE FROM face_data WHERE name = %s", (name,))
        deleted = cur.rowcount or 0
        cn.commit()
    return deleted

# ===== DeepFace utils =====
_model_lock = threading.Lock()
_model_warmed = False

def _warmup():
    global _model_warmed
    if not _HAVE_DF or _model_warmed:
        return
    with _model_lock:
        if _model_warmed:
            return
        dummy = np.zeros((160, 160, 3), dtype=np.uint8)
        try:
            DeepFace.represent(
                img_path=dummy,
                model_name=MODEL_NAME,
                detector_backend="opencv",
                enforce_detection=False
            )
            _model_warmed = True
        except Exception:
            pass

def detect_and_crop_face(frame_bgr: np.ndarray, align: bool = True) -> Optional[np.ndarray]:
    if frame_bgr is None:
        return None

    if _HAVE_DF:
        backends = [DETECTOR_BACKEND]
        for b in ("mtcnn", "retinaface", "opencv"):
            if b not in backends:
                backends.append(b)
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        for be in backends:
            try:
                faces = DeepFace.extract_faces(
                    img_path=rgb,
                    target_size=(160, 160),
                    detector_backend=be,
                    enforce_detection=False,
                    align=align
                )
                if faces:
                    best = max(
                        faces,
                        key=lambda f: f.get("facial_area", {}).get("w", 0)
                                      * f.get("facial_area", {}).get("h", 0)
                    )
                    face_rgb = (best["face"] * 255).astype("uint8")
                    return cv2.cvtColor(face_rgb, cv2.COLOR_RGB2BGR)
            except Exception:
                pass

    # Haar fallback
    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
        if len(faces) > 0:
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            pad = int(0.08 * max(w, h))
            x0 = max(0, x - pad)
            y0 = max(0, y - pad)
            x1 = min(frame_bgr.shape[1], x + w + pad)
            y1 = min(frame_bgr.shape[0], y + h + pad)
            face = frame_bgr[y0:y1, x0:x1].copy()
            return cv2.resize(face, (160, 160), interpolation=cv2.INTER_AREA)
    except Exception:
        pass

    return None

def embedding_from_cropped_face(face_bgr: np.ndarray) -> Optional[np.ndarray]:
    if not _HAVE_DF or face_bgr is None:
        return None
    _warmup()
    try:
        reps = DeepFace.represent(
            img_path=face_bgr,
            model_name=MODEL_NAME,
            detector_backend="skip",
            enforce_detection=False,
        )
        if not reps:
            return None
        return np.array(reps[0]["embedding"], dtype=np.float32)
    except Exception:
        return None

def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    na = np.linalg.norm(a) + 1e-8
    nb = np.linalg.norm(b) + 1e-8
    return 1.0 - float(np.dot(a, b) / (na * nb))

def find_best_match(query_emb: np.ndarray, db_items: List[Tuple[int, str, np.ndarray]]):
    if query_emb is None or not db_items:
        return None, None, float("inf")
    best_id, best_name, best_d = None, None, 1e9
    for rid, name, emb in db_items:
        d = cosine_distance(query_emb, emb)
        if d < best_d:
            best_id, best_name, best_d = rid, name, d
    return best_id, best_name, float(best_d)

# ===== High-level =====

def enroll_from_frame(frame_bgr, name: str, save_cropped_path: Optional[str] = None) -> bool:
    face = detect_and_crop_face(frame_bgr, align=True)
    if face is None:
        return False

    emb = embedding_from_cropped_face(face)
    if emb is None:
        return False

    enroll_embedding(name, emb)

    if save_cropped_path:
        import os
        try:
            os.makedirs(os.path.dirname(save_cropped_path), exist_ok=True)
        except Exception:
            pass

        ok = False
        try:
            ok = cv2.imwrite(save_cropped_path, face)
        except Exception:
            ok = False

        if not ok:
            try:
                from PIL import Image
                rgb = cv2.cvtColor(face, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(save_cropped_path, format="JPEG", quality=95)
                ok = True
            except Exception:
                ok = False

        if not ok:
            return False

    return True


def recognize_with_box(frame_bgr, threshold: float = THRESHOLD):
    """
    Trả về: (matched: bool, best_name|None, dist: float, box: (x0,y0,x1,y1)|None)
    """
    if frame_bgr is None:
        return False, None, 1e9, None

    box = None
    face_crop = None

    if _HAVE_DF:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        backends = [DETECTOR_BACKEND]
        for b in ("mtcnn", "retinaface", "opencv"):
            if b not in backends:
                backends.append(b)
        for be in backends:
            try:
                faces = DeepFace.extract_faces(
                    img_path=rgb,
                    target_size=(160, 160),
                    detector_backend=be,
                    enforce_detection=False,
                    align=True
                )
                if faces:
                    best = max(
                        faces,
                        key=lambda f: f.get("facial_area", {}).get("w", 0)
                                      * f.get("facial_area", {}).get("h", 0)
                    )
                    area = best.get("facial_area") or {}
                    x = int(area.get("x", 0))
                    y = int(area.get("y", 0))
                    w = int(area.get("w", 0))
                    h = int(area.get("h", 0))
                    x0, y0 = max(0, x), max(0, y)
                    x1, y1 = max(0, x + w), max(0, y + h)
                    box = (x0, y0, x1, y1)
                    face_rgb = (best["face"] * 255).astype("uint8")
                    face_crop = cv2.cvtColor(face_rgb, cv2.COLOR_RGB2BGR)
                    break
            except Exception:
                pass

    if face_crop is None:
        try:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80))
            if len(faces) > 0:
                x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
                pad = int(0.08 * max(w, h))
                x0 = max(0, x - pad)
                y0 = max(0, y - pad)
                x1 = min(frame_bgr.shape[1], x + w + pad)
                y1 = min(frame_bgr.shape[0], y + h + pad)
                box = (x0, y0, x1, y1)
                crop = frame_bgr[y0:y1, x0:x1].copy()
                face_crop = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA)
        except Exception:
            pass

    if face_crop is None:
        return False, None, 1e9, None

    emb = embedding_from_cropped_face(face_crop)
    if emb is None:
        return False, None, 1e9, box

    db = list_embeddings()
    fid, fname, dist = find_best_match(emb, db)
    matched = (fid is not None) and (dist < float(threshold))
    return matched, fname, float(dist), box
