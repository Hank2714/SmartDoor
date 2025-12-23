# ui/manage.py
import os
import platform
import subprocess
from pathlib import Path
from datetime import datetime

import tkinter as tk
import ttkbootstrap as tb
from ttkbootstrap.constants import *

from PIL import Image, ImageTk

from services.log_service import list_logs_by_month
from services.face_service import enroll_from_frame, delete_embeddings_by_name

# Dùng dialog enroll face giống bên HomeTab
from ui.home import EnrollFaceDialog


def truncate_all_tables():
    """
    Xoá sạch toàn bộ dữ liệu trong các bảng chính.
    Cảnh báo: KHÔNG xoá structure, chỉ làm TRUNCATE.
    """
    from db.db_conn import get_conn
    tables = ["access_log", "face_data", "fingerprint_data", "passcodes"]
    with get_conn() as cn, cn.cursor() as cur:
        for t in tables:
            try:
                cur.execute(f"TRUNCATE TABLE {t}")
            except Exception as e:
                print(f"[truncate] Fail {t}: {e}")
        cn.commit()


# --- Toast helper dùng chung ---
try:
    from ttkbootstrap.toast import ToastNotification

    def show_toast(title, msg, ms=2000, where="bottom-right", xmargin=16, ymargin=64):
        anchor = "se" if where == "bottom-right" else "ne"
        try:
            ToastNotification(
                title=title,
                message=msg,
                duration=ms,
                position=(xmargin, ymargin, anchor),
            ).show_toast()
        except Exception:
            from tkinter import messagebox
            messagebox.showinfo(title, msg)

except Exception:
    from tkinter import messagebox

    def show_toast(title, msg, ms=2000, where="bottom-right", xmargin=16, ymargin=64):
        messagebox.showinfo(title, msg)


def _open_in_explorer(path: Path):
    try:
        if platform.system() == "Windows":
            os.startfile(path)  # type: ignore
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        show_toast("Open folder", f"Cannot open: {e}")


class ManageTab(tb.Frame):
    def __init__(self, master, controller):
        super().__init__(master, padding=12)
        self.controller = controller
        self._preview_imgtk = None

        # faces dir (same as HomeTab)
        self.faces_dir = Path(__file__).resolve().parents[1] / "faces"
        self.faces_dir.mkdir(parents=True, exist_ok=True)

        self._build()

        # refresh initial
        self._refresh_logs()
        self._refresh_faces()

        # auto refresh logs
        self.after(5000, self._auto_refresh_logs)

    # ====================== BUILD UI ======================
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        # --------- LEFT: LOGS ----------
        lf_logs = tb.Labelframe(self, text="Access logs (by month)", padding=10)
        lf_logs.grid(row=0, column=0, sticky=NSEW, padx=(0, 12), pady=(0, 12))
        lf_logs.columnconfigure(0, weight=1)
        lf_logs.rowconfigure(2, weight=1)

        top = tb.Frame(lf_logs)
        top.grid(row=0, column=0, sticky=EW, pady=(0, 8))

        tb.Label(top, text="Month:").pack(side=LEFT)
        self.var_month = tk.IntVar(value=datetime.now().month)
        tb.Combobox(
            top,
            values=[i for i in range(1, 13)],
            width=4,
            textvariable=self.var_month,
            state="readonly",
        ).pack(side=LEFT, padx=(6, 12))

        tb.Label(top, text="Year:").pack(side=LEFT)
        self.var_year = tk.IntVar(value=datetime.now().year)
        tb.Spinbox(top, from_=2000, to=2100, width=6, textvariable=self.var_year).pack(
            side=LEFT, padx=(6, 12)
        )

        tb.Button(top, text="Refresh", bootstyle=INFO, command=self._refresh_logs).pack(side=LEFT)
        tb.Button(top, text="Clear logs", bootstyle=DANGER, command=self._clear_logs_month).pack(side=LEFT, padx=(6, 0))
        tb.Button(top, text="TRUNCATE ALL", bootstyle="danger-outline", command=self._truncate_all).pack(side=LEFT, padx=(10, 0))

        self.tv_logs = tb.Treeview(
            lf_logs,
            columns=("time", "method", "result"),
            show="headings",
            height=12,
        )
        self.tv_logs.heading("time", text="Time")
        self.tv_logs.heading("method", text="Method")
        self.tv_logs.heading("result", text="Result")
        self.tv_logs.column("time", width=160, anchor="center")
        self.tv_logs.column("method", width=100, anchor="center")
        self.tv_logs.column("result", width=100, anchor="center")
        self.tv_logs.grid(row=2, column=0, sticky=NSEW)

        # --------- RIGHT: FACES ----------
        lf_faces = tb.Labelframe(self, text="Faces", padding=10)
        lf_faces.grid(row=0, column=1, sticky=NSEW, pady=(0, 12))
        lf_faces.columnconfigure(0, weight=1)
        lf_faces.columnconfigure(1, weight=1)
        lf_faces.rowconfigure(1, weight=1)

        tl = tb.Frame(lf_faces)
        tl.grid(row=0, column=0, columnspan=2, sticky=EW, pady=(0, 8))

        tb.Button(
            tl,
            text="Open folder",
            bootstyle=SECONDARY,
            command=lambda: _open_in_explorer(self.faces_dir),
        ).pack(side=LEFT)

        tb.Button(
            tl,
            text="Enroll by camera…",
            bootstyle=SUCCESS,
            command=self._add_face_from_camera,
        ).pack(side=LEFT, padx=6)

        tb.Button(
            tl,
            text="Refresh",
            bootstyle=INFO,
            command=self._refresh_faces,
        ).pack(side=LEFT, padx=(6, 0))

        tb.Button(
            tl,
            text="Delete selected",
            bootstyle=DANGER,
            command=self._delete_selected_face,
        ).pack(side=LEFT, padx=(6, 0))

        self.tv_faces = tb.Treeview(lf_faces, columns=("file", "name"), show="headings", height=12)
        self.tv_faces.heading("file", text="Filename")
        self.tv_faces.heading("name", text="Name (from filename)")
        self.tv_faces.column("file", width=240, anchor="w")
        self.tv_faces.column("name", width=180, anchor="w")
        self.tv_faces.grid(row=1, column=0, sticky=NSEW, padx=(0, 8))

        self.prev_label = tb.Label(lf_faces, text="(Preview)")
        self.prev_label.grid(row=1, column=1, sticky=NSEW)

        self.tv_faces.bind("<<TreeviewSelect>>", lambda e: self._show_preview())
        self.tv_faces.bind("<Delete>", lambda e: self._delete_selected_face())

    # ====================== LOGS ======================
    def _refresh_logs(self):
        month = int(self.var_month.get())
        year = int(self.var_year.get())
        rows = list_logs_by_month(year, month)

        for iid in self.tv_logs.get_children(""):
            self.tv_logs.delete(iid)

        for r in rows:
            ts = r.get("timestamp")
            tstr = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts)
            vals = (tstr, r.get("method", ""), r.get("result", ""), r.get("passcode_masked", ""))
            self.tv_logs.insert("", "end", values=vals)

    def _auto_refresh_logs(self):
        try:
            self._refresh_logs()
        except Exception:
            pass
        self.after(5000, self._auto_refresh_logs)

    def _clear_logs_month(self):
        from tkinter import messagebox
        ans = messagebox.askyesno("Confirm", "Delete all logs for this month?")
        if not ans:
            return
        try:
            from services.log_service import clear_logs
            clear_logs(int(self.var_year.get()), int(self.var_month.get()))
            show_toast("Logs", "Logs cleared successfully")
            self._refresh_logs()
        except Exception as e:
            show_toast("Logs", f"Error clearing logs: {e}")

    def _truncate_all(self):
        from tkinter import messagebox
        msg = (
            "⚠️ WARNING!\n"
            "This will DELETE ALL DATA:\n"
            " • Access logs\n"
            " • Face embeddings\n"
            " • Fingerprints (table)\n"
            " • Passcodes\n\n"
            "Are you absolutely sure?"
        )
        if not messagebox.askyesno("TRUNCATE ALL", msg):
            return
        try:
            truncate_all_tables()
            show_toast("Database", "All tables truncated successfully.")
        except Exception as e:
            show_toast("Database", f"Error: {e}")

        self._refresh_logs()
        self._refresh_faces()

    # ====================== FACES ======================
    def _refresh_faces(self):
        for iid in self.tv_faces.get_children(""):
            self.tv_faces.delete(iid)

        files = []
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp"):
            files.extend(self.faces_dir.glob(ext))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        for p in files:
            name_guess = p.stem.split("_")[0] if "_" in p.stem else p.stem
            self.tv_faces.insert("", "end", iid=p.name, values=(p.name, name_guess))

        self._show_preview()

    def _show_preview(self):
        sel = self.tv_faces.selection()
        if not sel:
            self.prev_label.config(text="(Preview)", image="")
            self._preview_imgtk = None
            return

        fname = sel[0]
        path = self.faces_dir / fname
        if not path.exists():
            self.prev_label.config(text="(File not found)", image="")
            self._preview_imgtk = None
            return

        try:
            im = Image.open(path).convert("RGB")
            lw = max(180, self.prev_label.winfo_width() or 0)
            lh = max(180, self.prev_label.winfo_height() or 0)
            im.thumbnail((lw, lh), Image.LANCZOS)
            self._preview_imgtk = ImageTk.PhotoImage(im)
            self.prev_label.config(image=self._preview_imgtk, text="")
        except Exception as e:
            self.prev_label.config(text=f"(Preview error: {e})", image="")
            self._preview_imgtk = None

    def _delete_selected_face(self):
        sel = self.tv_faces.selection()
        if not sel:
            return

        fname = sel[0]
        path = self.faces_dir / fname
        name_guess = Path(fname).stem.split("_")[0] if "_" in Path(fname).stem else Path(fname).stem

        from tkinter import messagebox
        if not messagebox.askyesno(
            "Confirm delete",
            f"Delete face file + DB embeddings?\n\nFile: {fname}\nName: {name_guess}",
        ):
            return

        # DB: delete embeddings by name (your DB schema doesn't map 1 image -> 1 row)
        deleted_db = 0
        try:
            deleted_db = delete_embeddings_by_name(name_guess)
        except Exception as e:
            show_toast("Faces", f"DB delete error: {e}")

        # File
        try:
            if path.exists():
                path.unlink()
        except Exception as e:
            show_toast("Faces", f"File delete error: {e}")

        self._refresh_faces()
        show_toast("Faces", f"Deleted. DB rows={deleted_db}")

    def _add_face_from_camera(self):
        """
        Enroll using current frame from HomeTab camera (shared).
        """
        # App.py truyền controller=self (App), nên HomeTab nằm ở controller.home_tab
        ht = getattr(self.controller, "home_tab", None)
        if ht is None or not hasattr(ht, "_get_last_frame"):
            show_toast("Enroll", "Camera from Home tab is not available.")
            return

        dlg = EnrollFaceDialog(self, ht._get_last_frame)
        self.wait_window(dlg)
        ok, name, frame_bgr = dlg.result
        if not ok or frame_bgr is None or not name:
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = self.faces_dir / f"{name}_{ts}.jpg"
        ok2 = enroll_from_frame(frame_bgr, name, save_cropped_path=str(out.resolve()))
        if not ok2:
            show_toast("Enroll", "Failed to detect/align face")
            return

        show_toast("Enroll", f"Saved cropped face: {out.name}")
        self._refresh_faces()

    # ======= Public API for App.py tab-change refresh =======
    def refresh_faces(self):
        self._refresh_faces()
