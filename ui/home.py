# ui/home.py
from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime
import time
from typing import Optional, Any, Callable

import tkinter as tk
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog, messagebox, Toplevel

import numpy as np
from PIL import Image, ImageTk
import cv2

from services.log_service import log_access, get_recent_openings
from services.settings_service import get_all_settings, update_hold_time, set_toggle
from services.passcode_service import (
    has_main_passcode, create_temp_passcode, create_one_time_passcode,
    set_main_passcode, list_active_guest_codes,
    reveal_main_passcode, reveal_guest_passcode, delete_guest_passcode
)
from services.door_controller import DoorController
from services.camera_daemon import CameraDaemon
from services.recog_daemon import RecognitionDaemon
from services.face_service import enroll_from_frame

# --- Optional MTCNN face crop (giống base project) ---
try:
    from mtcnn import MTCNN as _MTCNN_pkg
    _HAS_MTCNN = True
except Exception:
    _HAS_MTCNN = False
    _MTCNN_pkg = None  # type: ignore


try:
    from ttkbootstrap.toast import ToastNotification

    def show_toast(title, msg, ms=2000, where="bottom-right", xmargin=16, ymargin=64):
        anchor = "se" if where == "bottom-right" else "ne"
        try:
            ToastNotification(title=title, message=msg, duration=ms,
                              position=(xmargin, ymargin, anchor)).show_toast()
        except Exception:
            messagebox.showinfo(title, msg)
except Exception:
    def show_toast(title, msg, ms=2000, where="bottom-right", xmargin=16, ymargin=64):
        messagebox.showinfo(title, msg)


def _center_on_parent(win: Toplevel):
    """Đặt cửa sổ vào giữa parent (nếu có) hoặc giữa màn hình (giống base dialogs.py)."""
    try:
        win.update_idletasks()
        parent = win.master if win.master and win.master.winfo_exists() else None
        if parent:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            ww = win.winfo_width()
            wh = win.winfo_height()
            x = px + (pw - ww) // 2
            y = py + (ph - wh) // 2
        else:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            ww = win.winfo_width()
            wh = win.winfo_height()
            x = (sw - ww) // 2
            y = (sh - wh) // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass


# ======================================================================
# EnrollFaceDialog  (dựa base CreateEmployeeDialog / ChangeFaceDialog)
# ======================================================================
class EnrollFaceDialog(Toplevel):
    """
    Popup enroll face:
      - Name *
      - Live preview từ camera (supplied bởi last_frame_supplier)
      - Capture / Upload / Retake
    result = (ok: bool, name: str, frame_bgr: np.ndarray | None)
    """

    def __init__(self, parent, last_frame_supplier: Callable[[], Optional[Any]]):
        super().__init__(parent)
        self.title("Enroll Face")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        self._last_frame_supplier = last_frame_supplier
        self._captured_img: Optional[Image.Image] = None   # PIL RGB sau khi crop
        self._preview_tk: Optional[ImageTk.PhotoImage] = None
        self._mode_camera_view = True

        frm = tb.Frame(self)
        frm.pack(fill=BOTH, expand=YES, padx=12, pady=10)

        # ---- Name ----
        tb.Label(frm, text="Name *").grid(row=0, column=0, sticky=E, padx=6, pady=6)
        self.ent_name = tb.Entry(frm, width=24)
        self.ent_name.grid(row=0, column=1, sticky=W, padx=6, pady=6)

        # ---- Face box ----
        cam_box = tb.Labelframe(frm, text="Face (capture or upload)")
        cam_box.grid(row=0, column=2, rowspan=3, sticky=NS, padx=(12, 0), pady=6)

        self.canvas = tb.Canvas(cam_box, width=280, height=210,
                                highlightthickness=0, bd=0)
        self.canvas.pack(padx=8, pady=(8, 4))

        row_btns = tb.Frame(cam_box)
        row_btns.pack(fill=X, padx=8, pady=(0, 8))
        self.btn_capture = tb.Button(
            row_btns, text="Capture", bootstyle=INFO,
            command=self._capture_from_camera
        )
        self.btn_upload = tb.Button(
            row_btns, text="Upload...", bootstyle=INFO,
            command=self._upload_file
        )
        self.btn_retake = tb.Button(
            row_btns, text="Retake", bootstyle=PRIMARY,
            command=self._retake, state=DISABLED
        )

        self.btn_capture.pack(side=LEFT, padx=2)
        self.btn_upload.pack(side=LEFT, padx=2)
        self.btn_retake.pack(side=LEFT, padx=8)

        # ---- Action buttons ----
        act = tb.Frame(self)
        act.pack(fill=X, padx=12, pady=(0, 10))
        self.btn_ok = tb.Button(
            act, text="Enroll", bootstyle=SUCCESS,
            command=self._do_enroll
        )
        self.btn_ok.pack(side=RIGHT, padx=4)
        tb.Button(
            act, text="Cancel", bootstyle=DANGER,
            command=self._cancel
        ).pack(side=RIGHT, padx=4)

        self.result: tuple[bool, str, Optional[np.ndarray]] = (False, "", None)

        self._poll_preview()
        _center_on_parent(self)

    # ----- live preview loop -----
    def _poll_preview(self):
        if self._mode_camera_view:
            frame = None
            try:
                frame = self._last_frame_supplier()
            except Exception:
                frame = None
            if frame is not None:
                self._render_bgr_to_canvas(frame)
            else:
                self.canvas.delete("all")
                self.canvas.create_text(
                    140, 105,
                    text="(Camera preview)",
                    fill="#999"
                )
        if self.winfo_exists():
            self.after(80, self._poll_preview)

    def _render_bgr_to_canvas(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = min(280 / max(1, w), 210 / max(1, h))
        nw, nh = int(w * scale), int(h * scale)
        img = Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR)
        self._preview_tk = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(140, 105, image=self._preview_tk)

    def _render_pil_on_canvas(self, im: Image.Image):
        w, h = im.size
        scale = min(280 / max(1, w), 210 / max(1, h))
        nw, nh = int(w * scale), int(h * scale)
        im2 = im.resize((nw, nh), Image.BILINEAR)
        self._preview_tk = ImageTk.PhotoImage(im2)
        self.canvas.delete("all")
        self.canvas.create_image(140, 105, image=self._preview_tk)

    def _crop_face_or_original(self, rgb: Any) -> Image.Image:
        """
        Crop bằng MTCNN nếu có (giống base dialogs.py),
        ngược lại trả về full image.
        """
        if not _HAS_MTCNN:
            return Image.fromarray(rgb)
        try:
            mtcnn = _MTCNN_pkg()
            res = mtcnn.detect_faces(rgb)
            best = None
            best_score = (-1, -1)
            for r in (res or []):
                conf = float(r.get("confidence", 0.0))
                x, y, w, h = r.get("box", [0, 0, 0, 0])
                if w <= 0 or h <= 0:
                    continue
                score = (conf, w * h)
                if score > best_score:
                    best_score = score
                    best = (x, y, w, h)
            if best is None:
                return Image.fromarray(rgb)
            x, y, w, h = best
            H, W = rgb.shape[:2]
            pad = int(0.12 * max(w, h))
            xa, ya = max(0, x - pad), max(0, y - pad)
            xb, yb = min(W, x + w + pad), min(H, y + h + pad)
            crop = rgb[ya:yb, xa:xb]
            if crop.size == 0:
                return Image.fromarray(rgb)
            return Image.fromarray(crop)
        except Exception:
            return Image.fromarray(rgb)

    # ----- actions -----
    def _capture_from_camera(self):
        frame = None
        try:
            frame = self._last_frame_supplier()
        except Exception:
            pass
        if frame is None:
            messagebox.showwarning("Camera", "Không có khung hình từ camera.")
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._captured_img = self._crop_face_or_original(rgb).convert("RGB")
        self._mode_camera_view = False
        self._render_pil_on_canvas(self._captured_img)
        self.btn_retake.config(state=NORMAL)

    def _upload_file(self):
        path = filedialog.askopenfilename(
            title="Chọn ảnh khuôn mặt",
            filetypes=[("Images", "*.jpg;*.jpeg;*.png;*.bmp;*.webp")]
        )
        if not path:
            return
        try:
            bgr = cv2.imread(path)
            if bgr is None:
                raise ValueError("Không đọc được ảnh.")
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        except Exception:
            messagebox.showwarning("Ảnh", "Tập tin ảnh không hợp lệ.")
            return

        self._captured_img = self._crop_face_or_original(rgb).convert("RGB")
        self._mode_camera_view = False
        self._render_pil_on_canvas(self._captured_img)
        self.btn_retake.config(state=NORMAL)

    def _retake(self):
        self._captured_img = None
        self._mode_camera_view = True
        self.btn_retake.config(state=DISABLED)

    def _do_enroll(self):
        name = (self.ent_name.get() or "").strip()
        if not name:
            messagebox.showwarning("Enroll", "Vui lòng nhập Name.")
            return
        if self._captured_img is None:
            messagebox.showwarning("Enroll", "Chưa có ảnh. Hãy Capture hoặc Upload trước.")
            return

        # Convert PIL RGB -> BGR numpy cho enroll_from_frame
        rgb_arr = np.array(self._captured_img.convert("RGB"))
        frame_bgr = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)

        self.result = (True, name, frame_bgr)
        self.destroy()

    def _cancel(self):
        self.result = (False, "", None)
        self.destroy()


class HomeTab(tb.Frame):
    """
    Home:
      - Live camera + face recognition (DeepFace), vẽ boundary box
      - Match 1 lần → log_access(method="face", result="granted") + gửi "open manual"
      - Nhận trạng thái cửa từ ESP32 qua UART ("Inform door opening/closed/...")
      - Manual open/close cũng dùng "open manual" / "close"
      - RecognitionDaemon: pause sau khi match; resume khi ESP32 báo cửa đã đóng
    """

    def __init__(self, master, controller: DoorController | tk.Misc):
        super().__init__(master, padding=12)
        self.controller = controller

        self._cam_imgtk: ImageTk.PhotoImage | None = None
        self._last_frame_bgr = None
        self._viz = None

        self._cam_daemon: CameraDaemon | None = None
        self._recog_daemon: RecognitionDaemon | None = None

        # door state
        self._door_state = "closed"
        self._door_busy = False

        self._status_var = tk.StringVar(value="Status: idle")
        self._fp_status_var = tk.StringVar(value="FP: idle")

        self.faces_dir = Path(__file__).resolve().parents[1] / "faces"
        os.makedirs(self.faces_dir, exist_ok=True)

        self._build_layout()
        self._init_camera_and_recognition()

        # đăng ký listener serial để nhận text từ ESP32
        try:
            if hasattr(self.controller, "add_listener"):
                self.controller.add_listener(self._on_serial_line)
        except Exception:
            pass

        self._load_settings()
        self._refresh_guest_table()
        self._refresh_recent_openings()

    # ---------- UI helpers ----------
    def _vc_digits(self, maxlen: int):
        def _check(P, ml=maxlen):
            return (P.isdigit() and len(P) <= ml) or P == ""
        return (self.register(_check), "%P")

    def _probe_cameras(self, max_index: int = 10):
        found = []
        for i in range(max_index):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            ok = cap.isOpened()
            if ok:
                ok, _ = cap.read()
            cap.release()
            if ok:
                found.append(str(i))
        if not found:
            found = ["0"]
        return found

    def _build_layout(self):
        self.columnconfigure(0, weight=3, uniform="cols")
        self.columnconfigure(1, weight=2, uniform="cols")
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        # keypad 4 số
        vc_pass = self._vc_digits(4)

        # LEFT: camera
        left = tb.Labelframe(self, text="Camera feed", padding=10)
        left.grid(row=0, column=0, sticky=NSEW, padx=(0, 12), pady=(0, 12))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.cam_label = tb.Label(left, text="(Video preview here)")
        self.cam_label.grid(row=0, column=0, sticky=NSEW)

        # Camera selector (góc phải)
        cam_ctrl = tb.Frame(left)
        cam_ctrl.place(relx=1.0, rely=0.0, anchor="ne")
        tb.Label(cam_ctrl, text="Camera:").pack(side=LEFT, padx=(0, 6))
        self.cam_idx_var = tk.StringVar(value=os.getenv("CAMERA_INDEX", "0"))
        self.cam_idx_cb = tb.Combobox(
            cam_ctrl, width=4, state="readonly",
            textvariable=self.cam_idx_var, values=self._probe_cameras()
        )
        self.cam_idx_cb.pack(side=LEFT)
        tb.Button(
            cam_ctrl, text="Refresh", bootstyle=SECONDARY,
            command=lambda: self.cam_idx_cb.configure(values=self._probe_cameras())
        ).pack(side=LEFT, padx=6)
        tb.Button(
            cam_ctrl, text="Apply", bootstyle=PRIMARY,
            command=self._apply_camera_index
        ).pack(side=LEFT)

        status_row = tb.Frame(left)
        status_row.grid(row=1, column=0, sticky=EW, pady=(8, 0))
        tb.Label(status_row, textvariable=self._status_var,
                 bootstyle=SECONDARY).pack(anchor=W)

        # Enroll button
        enroll = tb.Frame(left)
        enroll.grid(row=2, column=0, sticky=EW, pady=(10, 0))
        tb.Button(
            enroll, text="Enroll face…", bootstyle=SUCCESS,
            command=self._open_enroll_dialog
        ).pack(anchor=W)

        # RIGHT: door control + toggles + fingerprint box
        right = tb.Labelframe(self, text="Door control", padding=12)
        right.grid(row=0, column=1, sticky=NSEW, pady=(0, 12))
        right.columnconfigure(0, weight=1)

        btns = tb.Frame(right)
        btns.grid(row=0, column=0, sticky=EW, pady=(0, 10))
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)
        tb.Button(
            btns, text="Open door", bootstyle=SUCCESS,
            command=self._manual_open
        ).grid(row=0, column=0, sticky=EW, padx=(0, 8))
        tb.Button(
            btns, text="Close door", bootstyle=DANGER,
            command=self._manual_close
        ).grid(row=0, column=1, sticky=EW, padx=(8, 0))

        hold = tb.Frame(right)
        hold.grid(row=1, column=0, sticky=EW, pady=(0, 10))
        tb.Label(hold, text="Hold time (seconds)").pack(side=LEFT)
        self.hold_var = tk.IntVar(value=5)
        self.hold_entry = tb.Entry(
            hold, textvariable=self.hold_var, width=6, justify="center"
        )
        self.hold_entry.pack(side=LEFT, padx=(8, 8))
        tb.Button(
            hold, text="Save", bootstyle=INFO,
            command=self._save_hold
        ).pack(side=LEFT)
        self.scale = tb.Scale(
            right, from_=2, to=300, orient=HORIZONTAL,
            command=lambda v: self.hold_var.set(int(float(v)))
        )
        self.scale.set(5)
        self.scale.grid(row=2, column=0, sticky=EW, pady=(0, 10))

        um = tb.Labelframe(right, text="Unlock methods", padding=10)
        um.grid(row=3, column=0, sticky=EW)
        self.var_face = tk.BooleanVar(value=True)
        self.var_fp = tk.BooleanVar(value=True)
        self.var_code = tk.BooleanVar(value=True)
        tb.Checkbutton(
            um, text="Face recognition", variable=self.var_face,
            bootstyle="success-round-toggle", command=self._save_toggles
        ).pack(anchor=W, pady=2, fill=X)
        tb.Checkbutton(
            um, text="Fingerprint", variable=self.var_fp,
            bootstyle="success-round-toggle", command=self._save_toggles
        ).pack(anchor=W, pady=2, fill=X)
        tb.Checkbutton(
            um, text="Passcode", variable=self.var_code,
            bootstyle="success-round-toggle", command=self._save_toggles
        ).pack(anchor=W, pady=2, fill=X)

        # Fingerprint control + mini log
        fp_box = tb.Labelframe(right, text="Fingerprint", padding=8)
        fp_box.grid(row=4, column=0, sticky=EW, pady=(8, 0))
        fp_box.columnconfigure(0, weight=1)

        fp_btn_row = tb.Frame(fp_box)
        fp_btn_row.grid(row=0, column=0, sticky=EW, pady=(0, 4))
        fp_btn_row.columnconfigure(0, weight=1)

        tb.Button(
            fp_btn_row, text="Enroll fingerprint", bootstyle=SUCCESS,
            command=self._fp_enroll
        ).grid(row=0, column=0, sticky=EW)

        # Label slot (hiện tại chưa dùng, để sẵn)
        self.fp_slot_label = tb.Label(
            fp_box, text="First empty slot", bootstyle=SECONDARY, anchor=W
        )
        self.fp_slot_label.grid(row=1, column=0, sticky=EW, pady=(2, 4))

        # Dòng trạng thái fingerprint (dùng StringVar)
        self.fp_status_label = tb.Label(
            fp_box, textvariable=self._fp_status_var,
            bootstyle=SECONDARY, anchor=W
        )
        self.fp_status_label.grid(row=2, column=0, sticky=EW, pady=(0, 6))

        # Mini log: recent door openings
        tb.Label(fp_box, text="Recent door openings").grid(
            row=3, column=0, sticky=W, pady=(0, 2)
        )

        log_frame = tb.Frame(fp_box)
        log_frame.grid(row=4, column=0, sticky=EW)

        self.recent_tree = tb.Treeview(
            log_frame, columns=("time", "method"),
            show="headings", height=4
        )
        self.recent_tree.heading("time", text="Time")
        self.recent_tree.heading("method", text="Method")
        self.recent_tree.column("time", width=150, anchor="w")
        self.recent_tree.column("method", width=100, anchor="center")
        self.recent_tree.pack(side=LEFT, fill=BOTH, expand=YES)

        vsb = tb.Scrollbar(
            log_frame, orient=VERTICAL, command=self.recent_tree.yview
        )
        vsb.pack(side=RIGHT, fill=Y)
        self.recent_tree.configure(yscrollcommand=vsb.set)

        # BOTTOM: passcodes
        bottom = tb.Labelframe(self, text="Manage passcode", padding=10)
        bottom.grid(row=1, column=0, columnspan=2, sticky=EW)

        # ép 2 cột luôn bằng nhau, không bị lệch khi Treeview đổi kích thước
        bottom.columnconfigure(0, weight=1, uniform="passcols")
        bottom.columnconfigure(1, weight=1, uniform="passcols")

        # ----- LEFT: main + guest passcode -----
        leftp = tb.Frame(bottom)
        leftp.grid(row=0, column=0, sticky=NSEW, padx=(0, 10))
        leftp.columnconfigure(1, weight=1)

        tb.Label(leftp, text="Main Passcode:").grid(
            row=0, column=0, sticky=W, pady=2)
        self.main_entry = tb.Entry(leftp, show="*")
        self.main_entry.grid(row=0, column=1, sticky=EW, padx=6)
        self.main_entry.configure(validate="key", validatecommand=vc_pass)

        eye = tb.Button(leftp, text="👁", width=3)
        eye.grid(row=0, column=2, padx=(0, 6))

        # Nút Save cho main passcode
        tb.Button(
            leftp, text="Save", bootstyle=SUCCESS,
            command=self._save_main_passcode
        ).grid(row=0, column=3, padx=(0, 0))

        def _press_eye(_=None):
            plain = reveal_main_passcode()
            if plain:
                self.main_entry.config(show="")
                self.main_entry.delete(0, tk.END)
                self.main_entry.insert(0, plain)

        def _release_eye(_=None):
            self.main_entry.delete(0, tk.END)
            self.main_entry.config(show="*")

        eye.bind("<ButtonPress-1>", _press_eye)
        eye.bind("<ButtonRelease-1>", _release_eye)
        eye.bind("<Leave>", _release_eye)

        self.main_status = tb.Label(
            leftp, text="Main passcode: Not set", bootstyle=SECONDARY
        )
        self.main_status.grid(
            row=1, column=0, columnspan=4, sticky=W, pady=(0, 6)
        )

        tb.Label(leftp, text="Guest Passcode:").grid(
            row=2, column=0, sticky=W, pady=2
        )
        self.guest_entry = tb.Entry(leftp)
        self.guest_entry.grid(row=2, column=1, sticky=EW, padx=6)
        self.guest_entry.configure(validate="key", validatecommand=vc_pass)
        tb.Button(
            leftp, text="Copy", bootstyle=INFO,
            command=self._copy_guest
        ).grid(row=2, column=2, padx=(0, 4))

        opt = tb.Frame(leftp)
        opt.grid(row=3, column=0, columnspan=4, sticky=W, pady=(8, 0))
        self.var_one_time = tk.BooleanVar(value=False)
        tb.Checkbutton(
            opt, text="1 time use only", variable=self.var_one_time,
            bootstyle="info-round-toggle"
        ).pack(side=LEFT)
        tb.Label(opt, text="Valid for (minutes):").pack(
            side=LEFT, padx=(12, 6)
        )
        self.minutes_entry = tb.Entry(opt, width=8)
        self.minutes_entry.insert(0, "60")
        self.minutes_entry.pack(side=LEFT)

        tb.Button(
            opt, text="Generate code", bootstyle=INFO,
            command=self._gen_guest
        ).pack(side=LEFT, padx=10)

        # ----- RIGHT: guest list + countdown -----
        rightp = tb.Frame(bottom)
        rightp.grid(row=0, column=1, sticky=NSEW)   # <= N S E W
        rightp.columnconfigure(0, weight=1)
        rightp.rowconfigure(1, weight=1)            # Treeview fill theo chiều dọc

        tb.Label(rightp, text="Active guest passcodes (countdown)").grid(
            row=0, column=0, sticky=W, pady=(0, 6))

        self.tree = tb.Treeview(
            rightp, columns=("code", "remain"),
            show="headings", height=6
        )
        self.tree.heading("code", text="Passcode")
        self.tree.heading("remain", text="Remaining (mm:ss)")
        self.tree.column("code", width=160, anchor="w")
        self.tree.column("remain", width=150, anchor="center")
        self.tree.grid(row=1, column=0, sticky=NSEW)   # <= N S E W

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Copy", command=self._copy_selected)
        self.menu.add_command(label="Delete", command=self._delete_selected)

        def _popup(ev):
            iid = self.tree.identify_row(ev.y)
            if iid:
                self.tree.selection_set(iid)
                self.menu.tk_popup(ev.x_root, ev.y_root)

        self.tree.bind("<Button-3>", _popup)
        self.tree.bind("<Button-2>", _popup)

        self.after(0, self._update_cam_preview)

    # ---------- Camera + Recog ----------
    def _init_camera_and_recognition(self):
        self._cam_daemon = CameraDaemon(
            cam_index=int(self.cam_idx_var.get() or 0),
            on_frame=self._on_camera_frame,
            on_status=self._set_status,
            target_fps=30,
            width=640,
            height=480,
        )
        self._cam_daemon.start()

        self._recog_daemon = RecognitionDaemon(
            last_frame_supplier=self._get_last_frame,
            on_status=lambda s: self._set_status(
                "Face: " + s if not s.startswith("Face:") else s
            ),
            on_hit=self._on_face_hit_once,
            on_visual=self._set_viz,
            period_sec=0.8,
            threshold=0.30,
        )
        self._recog_daemon.start()

    def _on_camera_frame(self, frame_bgr):
        self._last_frame_bgr = frame_bgr

    def _get_last_frame(self):
        return self._last_frame_bgr

    def _set_status(self, text: str):
        try:
            self._status_var.set(text)
        except Exception:
            pass

    def _set_viz(self, viz):
        def _do():
            self._viz = viz

        try:
            self.after(0, _do)
        except Exception:
            pass

    def _apply_camera_index(self):
        try:
            new_idx = int(self.cam_idx_var.get())
        except Exception:
            show_toast("Camera", "Invalid camera index")
            return

        try:
            if self._recog_daemon:
                self._recog_daemon.pause()
        except Exception:
            pass

        try:
            if self.cam_label:
                self.cam_label.configure(image="")
                self.cam_label.update_idletasks()
        except Exception:
            pass

        try:
            if self._cam_daemon:
                self._cam_daemon.stop()
        except Exception:
            pass
        self._cam_daemon = None

        self._last_frame_bgr = None
        self._viz = None
        self._cam_imgtk = None
        self.cam_label.configure(text="(Switching camera...)")

        self._cam_daemon = CameraDaemon(
            cam_index=new_idx,
            on_frame=self._on_camera_frame,
            on_status=self._set_status,
            target_fps=30,
            width=640,
            height=480,
        )
        self._cam_daemon.start()
        show_toast("Camera", f"Switched to index {new_idx}")

        try:
            if self._recog_daemon:
                self._recog_daemon.resume()
        except Exception:
            pass

    # ---------- Preview + overlay ----------
    def _update_cam_preview(self):
        try:
            frame = self._last_frame_bgr
            if frame is not None:
                draw = frame.copy()
                if self._viz and (time.time() - float(self._viz.get("ts", 0))) <= 1.5:
                    x0, y0, x1, y1 = self._viz["box"]
                    color = tuple(int(c) for c in self._viz.get("color", (0, 255, 0)))
                    label = str(self._viz.get("label", "") or "")
                    cv2.rectangle(
                        draw, (int(x0), int(y0)), (int(x1), int(y1)), color, 2
                    )
                    if label:
                        (tw, th), _ = cv2.getTextSize(
                            label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
                        )
                        cv2.rectangle(
                            draw,
                            (int(x0), max(0, int(y0) - th - 8)),
                            (int(x0) + tw + 6, int(y0)),
                            color,
                            -1,
                        )
                        cv2.putText(
                            draw,
                            label,
                            (int(x0) + 3, int(y0) - 5),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            (0, 0, 0),
                            1,
                            cv2.LINE_AA,
                        )
                else:
                    self._viz = None

                frame_rgb = cv2.cvtColor(draw, cv2.COLOR_BGR2RGB)
                lw = max(200, self.cam_label.winfo_width() or 0)
                lh = max(150, self.cam_label.winfo_height() or 0)
                ih, iw = frame_rgb.shape[:2]
                scale = min(lw / max(1, iw), lh / max(1, ih))
                nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
                resized = cv2.resize(
                    frame_rgb, (nw, nh), interpolation=cv2.INTER_AREA
                )

                canvas = Image.new("RGB", (lw, lh), (30, 30, 30))
                pil_img = Image.fromarray(resized)
                ox, oy = (lw - nw) // 2, (lh - nh) // 2
                canvas.paste(pil_img, (ox, oy))
                self._cam_imgtk = ImageTk.PhotoImage(canvas)
                if self.cam_label.cget("text"):
                    self.cam_label.configure(text="")
                self.cam_label.configure(image=self._cam_imgtk)
            else:
                if self.cam_label.cget("image"):
                    self.cam_label.configure(image="")
                if not self.cam_label.cget("text"):
                    self.cam_label.configure(text="(No frame)")
        except Exception as e:
            try:
                self.cam_label.configure(image="")
            except Exception:
                pass
            self.cam_label.configure(text=f"(Camera error: {e})")
        finally:
            self.after(33, self._update_cam_preview)

    # ---------- Door triggers ----------
    def _on_face_hit_once(self, name: str, dist: float):
        try:
            log_access(
                method="face",
                result="granted",
                confidence=max(0.0, float(dist)),
            )
        except Exception:
            pass

        # Gửi lệnh mở cửa nếu đang đóng
        if not self._door_busy and self._door_state == "closed":
            try:
                if hasattr(self.controller, "open_door"):
                    self.controller.open_door()
            except Exception:
                pass
            self._door_state = "opening"
            self._door_busy = True
            self._set_app_status("Door state: opening (face)")

    def _set_app_status(self, text: str):
        try:
            if hasattr(self.controller, "set_status"):
                self.controller.set_status(text)
            elif hasattr(self.controller, "status"):
                self.controller.status.configure(text=text)
            else:
                self._status_var.set(text)
        except Exception:
            pass

    # ---------- Serial events từ ESP32 ----------
    def _on_serial_line(self, line: str):
        """
        Nhận 1 dòng raw từ ESP32 và cập nhật trạng thái UI + daemon.
        Khớp với main.py bạn đang chạy trên ESP32.
        """
        if not line:
            return
        text = line.strip()

        if "Inform door opening" in text:
            self._door_state = "opening"
            self._door_busy = True
            self._set_app_status("Door state: opening...")
            try:
                if self._recog_daemon:
                    self._recog_daemon.pause()
            except Exception:
                pass

        elif "Inform door opened" in text:
            self._door_state = "open_hold"
            self._set_app_status("Door state: open (waiting)")

        elif "Inform door closing" in text:
            self._door_state = "closing"
            self._set_app_status("Door state: closing...")

        elif "Inform door closed" in text:
            self._door_state = "closed"
            self._door_busy = False
            self._set_app_status("Door state: close")
            # ESP32 báo đã đóng -> cho phép nhận diện lại
            try:
                if self._recog_daemon:
                    self._recog_daemon.resume()
            except Exception:
                pass

        # fingerprint log / status
        elif text.startswith("Inform finger found"):
            self._set_status("FP: matched")
            self._fp_status_var.set(text)
        elif "Inform finger not found" in text:
            self._set_status("FP: no match")
            self._fp_status_var.set(text)
        elif text.startswith("Inform enroll"):
            self._fp_status_var.set(text)
        elif text.startswith("Inform delete"):
            self._fp_status_var.set(text)
        elif "Inform library first empty slot" in text:
            self._fp_status_var.set(text)
        elif text.startswith("Error enroll") or text.startswith("Error delete") or text.startswith("Error library"):
            self._fp_status_var.set(text)

    # ---------- Settings / Passcodes ----------
    def _load_settings(self):
        s = get_all_settings() or {}
        hold = int(s.get("hold_time", 5))
        hold = max(2, min(300, hold))
        self.hold_var.set(hold)
        self.scale.set(hold)

        # sync xuống DoorController
        try:
            if hasattr(self.controller, "set_hold_time"):
                self.controller.set_hold_time(hold)
        except Exception:
            pass

        self.var_face.set(bool(s.get("face_recognition_enabled", 1)))
        self.var_fp.set(bool(s.get("fingerprint_enabled", 1)))
        self.var_code.set(bool(s.get("passcode_enabled", 1)))
        self._update_main_status()
        self._set_app_status("Door state: close")
        self._door_state = "closed"
        self._door_busy = False

    def _update_main_status(self):
        self.main_status.configure(
            text="Main passcode: Set" if has_main_passcode()
            else "Main passcode: Not set"
        )

    def _save_main_passcode(self):
        code = (self.main_entry.get() or "").strip()
        if not code:
            show_toast("Passcode", "Please enter main passcode")
            return
        if (not code.isdigit()) or len(code) != 4:
            show_toast("Passcode", "Main passcode must be exactly 4 digits.")
            return

        try:
            set_main_passcode(code)   # bên trong vẫn validate lần nữa
            show_toast("Passcode", "Main passcode updated")
            self._update_main_status()
            self.main_entry.delete(0, tk.END)
        except Exception as e:
            show_toast("Passcode", f"Error: {e}")


    def _refresh_guest_table(self):
        rows = list_active_guest_codes()
        existing = set(self.tree.get_children(""))
        seen = set()
        for r in rows:
            iid = str(r["id"])
            seen.add(iid)
            m, s = divmod(int(r["remain_sec"]), 60)
            txt = f"{m:02d}:{s:02d}"
            code_display = r["code_masked"].replace(
                "****-", ""
            ) if r["code_masked"] else ""
            if iid in existing:
                self.tree.item(iid, values=(code_display, txt))
            else:
                self.tree.insert("", "end", iid=iid, values=(code_display, txt))
        for iid in existing - seen:
            self.tree.delete(iid)
        self.after(1000, self._refresh_guest_table)

    def _save_hold(self):
        try:
            v = int(self.hold_var.get())
        except ValueError:
            v = 5
        v = max(2, min(300, v))
        self.hold_var.set(v)
        self.scale.set(v)

        update_hold_time(v)

        try:
            if hasattr(self.controller, "set_hold_time"):
                self.controller.set_hold_time(v)
        except Exception:
            pass

        show_toast("Door", f"Hold time updated to {v} seconds")

    def _save_toggles(self):
        set_toggle("face_recognition_enabled", self.var_face.get())
        set_toggle("fingerprint_enabled", self.var_fp.get())
        set_toggle("passcode_enabled", self.var_code.get())

    # ---------- Fingerprint controls ----------
    def _fp_enroll(self):
        """Bắt đầu enroll fingerprint: gửi 'enroll' xuống ESP32."""
        try:
            if hasattr(self.controller, "send_raw"):
                self.controller.send_raw("enroll")
            self._fp_status_var.set("FP: enroll started...")
        except Exception as e:
            self._fp_status_var.set(f"FP error: {e}")

    # ---------- Manual buttons ----------
    def _manual_open(self):
        if self._door_busy:
            return
        try:
            if self._recog_daemon:
                self._recog_daemon.pause()
        except Exception:
            pass
        try:
            if hasattr(self.controller, "open_door"):
                self.controller.open_door()
        except Exception:
            pass
        self._door_state = "opening"
        self._door_busy = True
        self._set_app_status("Door state: opening (manual)")

    def _manual_close(self):
        try:
            if hasattr(self.controller, "close_door"):
                self.controller.close_door()
        except Exception:
            pass
        # trạng thái chính xác sẽ được cập nhật khi ESP32 in "Inform door closing"/"Inform door closed"

    # ---------- Tree menu ----------
    def _copy_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        plain = reveal_guest_passcode(pid)
        if not plain:
            return
        self.clipboard_clear()
        self.clipboard_append(plain)

    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        delete_guest_passcode(pid)
        self.after(100, self._refresh_guest_table)

    def _copy_guest(self):
        code = self.guest_entry.get().strip()
        if not code:
            show_toast("Copy", "No guest passcode to copy")
            return
        self.clipboard_clear()
        self.clipboard_append(code)
        show_toast("Copy", "Guest passcode copied!")

    def _gen_guest(self):
        import random
        try:
            minutes = int(self.minutes_entry.get().strip() or "60")
        except ValueError:
            minutes = 60
        minutes = max(1, min(24 * 60, minutes))
        self.minutes_entry.delete(0, tk.END)
        self.minutes_entry.insert(0, str(minutes))

        # nếu người dùng đã nhập code thì dùng code đó, ngược lại random
        raw = (self.guest_entry.get() or "").strip()
        if raw:
            # bắt buộc đúng 4 ký tự số
            if (not raw.isdigit()) or len(raw) != 4:
                show_toast("Guest passcode", "Guest passcode must be exactly 4 digits.")
                return
            code = raw
        else:
            # random đúng 4 số
            code = f"{random.randint(0, 9999):04d}"

        try:
            if self.var_one_time.get():
                create_one_time_passcode(code, minutes_valid=minutes)
                show_toast(
                    "Guest passcode",
                    f"Created 1-time code ({minutes} phút)"
                )
            else:
                create_temp_passcode(code, minutes_valid=minutes)
                show_toast(
                    "Guest passcode",
                    f"Created temporary code ({minutes} phút)"
                )
        except Exception as e:
            show_toast("Guest passcode", f"Error: {e}")
            return

        self.guest_entry.delete(0, tk.END)
        self.guest_entry.insert(0, code)
        try:
            self.clipboard_clear()
            self.clipboard_append(code)
        except Exception:
            pass
        self.after(150, self._refresh_guest_table)

    # ---------- Enroll popup ----------
    def _open_enroll_dialog(self):
        dlg = EnrollFaceDialog(self, self._get_last_frame)
        self.wait_window(dlg)
        ok, name, frame_bgr = dlg.result
        if not ok or frame_bgr is None or not name:
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = self.faces_dir / f"{name}_{ts}.jpg"
        out_abs = str(out.resolve())

        ok2 = enroll_from_frame(frame_bgr, name, save_cropped_path=out_abs)
        if not ok2:
            try:
                raw_path = self.faces_dir / f"__debug_raw_{ts}.jpg"
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                Image.fromarray(rgb).save(
                    str(raw_path), format="JPEG", quality=92
                )
                if raw_path.exists() and raw_path.stat().st_size > 0:
                    show_toast(
                        "Enroll",
                        f"Failed to detect/align face.\nRaw saved: {raw_path.name}",
                    )
                else:
                    show_toast(
                        "Enroll",
                        "Failed to detect/align face.\nAlso cannot save raw frame.",
                    )
            except Exception as e:
                show_toast(
                    "Enroll",
                    f"Failed to detect/align face.\nRaw save err: {e}",
                )
            return

        try:
            if out.exists() and out.stat().st_size > 0:
                show_toast("Enroll", f"Saved cropped face: {out.name}")
            else:
                show_toast(
                    "Enroll",
                    f"Enroll OK, but file not visible: {out.name}",
                )
        except Exception as e:
            show_toast(
                "Enroll",
                f"Enroll OK, but check file error: {e}",
            )

    def _refresh_recent_openings(self):
        """
        Cập nhật mini-log 'Recent door openings' từ access_log.
        Chỉ hiển thị các bản ghi result='granted'.
        """
        try:
            rows = get_recent_openings(limit=20)
        except Exception as e:
            print(f"[HomeTab] _refresh_recent_openings error: {e}")
            rows = []

        for iid in self.recent_tree.get_children(""):
            self.recent_tree.delete(iid)

        for r in rows:
            ts = r.get("ts")
            method = r.get("method") or ""
            if isinstance(ts, datetime):
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
            else:
                ts_str = str(ts) if ts is not None else ""
            self.recent_tree.insert("", "end", values=(ts_str, method))

        try:
            self.after(2000, self._refresh_recent_openings)
        except Exception:
            pass

    # ---------- Cleanup ----------
    def destroy(self):
        try:
            if self._recog_daemon:
                self._recog_daemon.stop()
        except Exception:
            pass
        self._recog_daemon = None

        try:
            if self._cam_daemon:
                self._cam_daemon.stop()
        except Exception:
            pass
        self._cam_daemon = None

        super().destroy()
