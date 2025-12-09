# services/camera_daemon.py
from __future__ import annotations
import sys
import time
import threading
from typing import Optional, Callable

import cv2


class CameraDaemon(threading.Thread):
    """
    Đọc camera ở background thread và đẩy frame cho UI qua callback.

    - on_frame(frame_bgr): callback mỗi khi có frame mới (frame BGR gốc)
    - on_status(text): cập nhật trạng thái text cho UI
    - Hỗ trợ đổi camera bằng set_camera(index)
    - Trên Windows: ưu tiên backend MSMF -> DSHOW -> ANY để hạn chế warning DSHOW.
    """

    def __init__(
        self,
        cam_index: int = 0,
        on_frame: Callable[[object], None] = lambda *_: None,
        on_status: Callable[[str], None] = lambda *_: None,
        target_fps: int = 30,
        width: int = 640,
        height: int = 480,
    ):
        super().__init__(daemon=True)
        self._idx = int(cam_index)
        self.on_frame = on_frame
        self.on_status = on_status
        self.target_fps = max(5, int(target_fps))
        self.width = int(width)
        self.height = int(height)

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._cap: Optional[cv2.VideoCapture] = None

        if sys.platform.startswith("win"):
            self._backends = [cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY]
        else:
            self._backends = [cv2.CAP_ANY]

    # ---------- public API ----------

    def set_camera(self, cam_index: int) -> None:
        """Đổi sang camera index khác trong lúc daemon đang chạy."""
        with self._lock:
            self._idx = int(cam_index)
            self._open_capture(self._idx)

    def stop(self) -> None:
        """Dừng thread và giải phóng camera."""
        self._stop.set()
        with self._lock:
            self._release_nolock()

    # ---------- internal camera open/close ----------

    def _release_nolock(self):
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def _open_capture(self, index: int) -> None:
        self._release_nolock()

        opened = False
        last_err = ""

        for be in self._backends:
            try:
                cap = cv2.VideoCapture(int(index), be)
                if not cap or not cap.isOpened():
                    if cap:
                        cap.release()
                    continue

                try:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                except Exception:
                    pass

                self._cap = cap
                opened = True
                be_name = {
                    cv2.CAP_MSMF: "MSMF",
                    cv2.CAP_DSHOW: "DSHOW",
                    cv2.CAP_ANY: "ANY",
                }.get(be, str(be))
                self.on_status(f"Camera: opened idx {index} via {be_name}")
                break
            except Exception as e:
                last_err = str(e)
                continue

        if not opened:
            self._cap = None
            msg = f"Camera: cannot open index {index}"
            if last_err:
                msg += f" ({last_err})"
            self.on_status(msg)

    # ---------- main loop ----------

    def run(self) -> None:
        self.on_status("Camera: starting…")
        with self._lock:
            self._open_capture(self._idx)

        frame_interval = 1.0 / float(self.target_fps)

        while not self._stop.is_set():
            t0 = time.perf_counter()

            with self._lock:
                cap = self._cap

            if cap is None or not cap.isOpened():
                self.on_status("Camera: not opened")
                time.sleep(0.5)
                continue

            ok, frame = cap.read()
            if not ok or frame is None:
                self.on_status("Camera: no frame")
                time.sleep(0.2)
            else:
                try:
                    self.on_frame(frame)
                except Exception:
                    pass

            dt = time.perf_counter() - t0
            if dt < frame_interval:
                time.sleep(frame_interval - dt)

        with self._lock:
            self._release_nolock()
        self.on_status("Camera: stopped")
