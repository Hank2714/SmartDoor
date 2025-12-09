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
import cv2

from services.log_service import list_logs_by_month
from services.face_service import enroll_from_frame, delete_embeddings_by_name
from services.fingerprint_service import (
    list_fingerprints, add_fingerprint_placeholder, delete_fingerprint
)

# Dùng dialog enroll face giống bên HomeTab
from ui.home import EnrollFaceDialog

def truncate_all_tables():
    """
    Xoá sạch toàn bộ dữ liệu trong các bảng chính.
    Cảnh báo: KHÔNG xoá structure, chỉ làm TRUNCATE.
    """
    from db.db_conn import get_conn
    tables = [
        "access_log",
        "face_data",
        "fingerprint_data",
        "passcodes"
    ]
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
            ToastNotification(title=title, message=msg, duration=ms,
                              position=(xmargin, ymargin, anchor)).show_toast()
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

        # faces dir
        self.faces_dir = Path(__file__).resolve().parents[1] / "faces"
        self.faces_dir.mkdir(parents=True, exist_ok=True)

        self._build()

        # refresh initial
        self._refresh_logs()
        self._refresh_faces()
        self._refresh_fps()

        # auto refresh định kỳ cho logs + fingerprints
        self.after(5000, self._auto_refresh_logs)
        self.after(5000, self._auto_refresh_fps)

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
        tb.Spinbox(
            top, from_=2000, to=2100, width=6, textvariable=self.var_year
        ).pack(side=LEFT, padx=(6, 12))

        tb.Button(
            top, text="Refresh", bootstyle=INFO, command=self._refresh_logs
        ).pack(side=LEFT)
        tb.Button(
            top, text="Clear logs", bootstyle=DANGER, command=self._clear_logs_month
        ).pack(side=LEFT, padx=(6, 0))

        tb.Button(
            top,
            text="TRUNCATE ALL",
            bootstyle="danger-outline",
            command=self._truncate_all
        ).pack(side=LEFT, padx=(10,0))

        self.tv_logs = tb.Treeview(
            lf_logs,
            columns=("time", "method", "result", "passcode"),
            show="headings",
            height=12,
        )
        self.tv_logs.heading("time", text="Time")
        self.tv_logs.heading("method", text="Method")
        self.tv_logs.heading("result", text="Result")
        self.tv_logs.heading("passcode", text="Passcode")
        self.tv_logs.column("time", width=160, anchor="center")
        self.tv_logs.column("method", width=100, anchor="center")
        self.tv_logs.column("result", width=100, anchor="center")
        self.tv_logs.column("passcode", width=140, anchor="center")
        self.tv_logs.grid(row=2, column=0, sticky=NSEW)

        # menu chuột phải cho logs
        self.menu_logs = tk.Menu(self, tearoff=0)
        self.menu_logs.add_command(label="Delete selected", command=self._delete_selected_log)

        def on_logs_popup(ev):
            iid = self.tv_logs.identify_row(ev.y)
            if iid:
                self.tv_logs.selection_set(iid)
                self.menu_logs.tk_popup(ev.x_root, ev.y_root)

        self.tv_logs.bind("<Button-3>", on_logs_popup)
        self.tv_logs.bind("<Button-2>", on_logs_popup)

        # --------- RIGHT: FACES + FINGERPRINT ----------
        right = tb.Frame(self)
        right.grid(row=0, column=1, sticky=NSEW, pady=(0, 12))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        # --- Faces ---
        lf_faces = tb.Labelframe(right, text="Faces", padding=10)
        lf_faces.grid(row=0, column=0, sticky=NSEW, pady=(0, 12))
        lf_faces.columnconfigure(0, weight=1)
        lf_faces.columnconfigure(1, weight=1)
        lf_faces.rowconfigure(1, weight=1)

        tl = tb.Frame(lf_faces)
        tl.grid(row=0, column=0, columnspan=2, sticky=EW, pady=(0, 8))

        # Mở thư mục chứa ảnh
        tb.Button(
            tl,
            text="Open folder",
            bootstyle=SECONDARY,
            command=lambda: _open_in_explorer(self.faces_dir)
        ).pack(side=LEFT)

        # ⭐ ONLY ONE BUTTON LEFT — Enroll by camera ⭐
        tb.Button(
            tl,
            text="Enroll by camera…",
            bootstyle=SUCCESS,
            command=self._add_face_from_camera
        ).pack(side=LEFT, padx=6)

        # Treeview danh sách ảnh
        self.tv_faces = tb.Treeview(
            lf_faces, columns=("file", "name"), show="headings", height=8
        )
        self.tv_faces.heading("file", text="Filename")
        self.tv_faces.heading("name", text="Name (from filename)")
        self.tv_faces.column("file", width=220, anchor="w")
        self.tv_faces.column("name", width=160, anchor="w")
        self.tv_faces.grid(row=1, column=0, sticky=NSEW, padx=(0, 8))

        # Preview
        self.prev_label = tb.Label(lf_faces, text="(Preview)")
        self.prev_label.grid(row=1, column=1, sticky=NSEW)

        # Context menu (rename + delete)
        self.menu_faces = tk.Menu(self, tearoff=0)
        self.menu_faces.add_command(label="Rename", command=self._face_rename)
        self.menu_faces.add_command(label="Delete", command=self._face_delete)

        def on_faces_popup(ev):
            iid = self.tv_faces.identify_row(ev.y)
            if iid:
                self.tv_faces.selection_set(iid)
                self.menu_faces.tk_popup(ev.x_root, ev.y_root)

        self.tv_faces.bind("<Button-3>", on_faces_popup)
        self.tv_faces.bind("<Button-2>", on_faces_popup)
        self.tv_faces.bind("<<TreeviewSelect>>", lambda e: self._show_preview())


        # --- Fingerprints ---
        lf_fp = tb.Labelframe(right, text="Fingerprints", padding=10)
        lf_fp.grid(row=1, column=0, sticky=NSEW)
        lf_fp.columnconfigure(0, weight=1)
        lf_fp.rowconfigure(1, weight=1)

        tf = tb.Frame(lf_fp)
        tf.grid(row=0, column=0, sticky=EW, pady=(0, 8))
        tb.Button(tf, text="Add", bootstyle=SUCCESS, command=self._fp_add).pack(side=LEFT)
        tb.Button(tf, text="Delete", bootstyle=DANGER, command=self._fp_delete).pack(
            side=LEFT, padx=6
        )

        tb.Button(
            tf, text="Delete ALL", bootstyle="danger-outline",
            command=self._fp_delete_all
        ).pack(side=LEFT, padx=(6,0))


        self.tv_fp = tb.Treeview(
            lf_fp, columns=("id", "name"), show="headings", height=8
        )
        self.tv_fp.heading("id", text="ID")
        self.tv_fp.heading("name", text="Name")
        self.tv_fp.column("id", width=60, anchor="center")
        self.tv_fp.column("name", width=200, anchor="w")
        self.tv_fp.grid(row=1, column=0, sticky=NSEW)

    # ====================== LOGIC: LOGS ======================
    def _refresh_logs(self):
        month = int(self.var_month.get())
        year = int(self.var_year.get())
        rows = list_logs_by_month(year, month)
        # clear cũ
        for iid in self.tv_logs.get_children(""):
            self.tv_logs.delete(iid)
        # fill
        for r in rows:
            rid = None
            if isinstance(r, dict) and "id" in r and r["id"] is not None:
                try:
                    rid = str(int(r["id"]))
                except Exception:
                    rid = None

            ts = r["timestamp"]
            if hasattr(ts, "strftime"):
                tstr = ts.strftime("%Y-%m-%d %H:%M:%S")
            else:
                tstr = str(ts)

            vals = (
                tstr,
                r.get("method", ""),
                r.get("result", ""),
                r.get("passcode_masked", ""),
            )
            if rid:
                self.tv_logs.insert("", "end", iid=rid, values=vals)
            else:
                self.tv_logs.insert("", "end", values=vals)

    def _auto_refresh_logs(self):
        """Auto-refresh logs mỗi 5 giây (khi ManageTab còn tồn tại)."""
        try:
            self._refresh_logs()
        except Exception:
            pass
        try:
            self.after(5000, self._auto_refresh_logs)
        except Exception:
            pass

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

    def _delete_selected_log(self):
        sel = self.tv_logs.selection()
        if not sel:
            return
        iid = sel[0]
        if not iid.isdigit():
            show_toast(
                "Logs",
                "This log row has no database id; cannot delete from DB.",
            )
            return
        try:
            from services.log_service import delete_log

            delete_log(int(iid))
            self.tv_logs.delete(iid)
            show_toast("Logs", "Deleted selected log")
        except Exception as e:
            show_toast("Logs", f"Delete error: {e}")

    def _truncate_all(self):
        from tkinter import messagebox
        msg = (
            "⚠️ WARNING!\n"
            "This will DELETE ALL DATA:\n"
            " • Access logs\n"
            " • Face embeddings\n"
            " • Fingerprints\n"
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

        # refresh UI
        self._refresh_logs()
        self._refresh_faces()
        self._refresh_fps()


    # ====================== LOGIC: FACES ======================
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
            lw = max(160, self.prev_label.winfo_width() or 0)
            lh = max(160, self.prev_label.winfo_height() or 0)
            im.thumbnail((lw, lh), Image.LANCZOS)
            self._preview_imgtk = ImageTk.PhotoImage(im)
            self.prev_label.config(image=self._preview_imgtk, text="")
        except Exception as e:
            self.prev_label.config(text=f"(Preview error: {e})", image="")
            self._preview_imgtk = None

    def _face_rename(self):
        sel = self.tv_faces.selection()
        if not sel:
            return
        old = sel[0]
        oldp = self.faces_dir / old
        if not oldp.exists():
            show_toast("Rename", "File not found")
            return
        win = tk.Toplevel(self)
        win.title("Rename face image")
        win.transient(self)
        win.grab_set()
        tb.Label(win, text="New filename (without extension):").pack(
            padx=12, pady=(12, 4), anchor=W
        )
        var = tk.StringVar(value=oldp.stem)
        tb.Entry(win, textvariable=var, width=40).pack(
            padx=12, pady=(0, 8)
        )

        def do_ok():
            newstem = var.get().strip()
            if not newstem:
                return
            newp = self.faces_dir / f"{newstem}{oldp.suffix.lower()}"
            try:
                oldp.rename(newp)
                show_toast("Rename", "Done")
            except Exception as e:
                show_toast("Rename", f"Error: {e}")
            win.destroy()
            self._refresh_faces()

        tb.Button(win, text="OK", bootstyle=PRIMARY, command=do_ok).pack(
            padx=12, pady=(0, 12)
        )
        tb.Button(win, text="Cancel", command=win.destroy).pack(
            padx=12, pady=(0, 12)
        )

    def _face_delete(self):
        sel = self.tv_faces.selection()
        if not sel:
            return
        fname = sel[0]
        vals = self.tv_faces.item(fname, "values") or ("", "")
        name_guess = (vals[1] or "").strip()  # cột "name"
        p = self.faces_dir / fname
        if not p.exists():
            show_toast("Delete", "File not found")
            return
        # 1) Xoá file
        try:
            p.unlink()
        except Exception as e:
            show_toast("Delete", f"File delete error: {e}")
            return
        # 2) Xoá embedding cùng tên trong DB (nếu có)
        deleted = 0
        try:
            if name_guess:
                deleted = delete_embeddings_by_name(name_guess)
        except Exception as e:
            show_toast("Faces DB", f"Delete embedding error: {e}")
        # 3) Báo UI + yêu cầu HomeTab reload recognizer
        msg = "Deleted"
        if deleted > 0:
            msg += f" • removed {deleted} embedding(s)"
        show_toast("Delete", msg)
        self._refresh_faces()
        try:
            if hasattr(self.controller, "home_tab") and hasattr(
                self.controller.home_tab, "force_reload_faces"
            ):
                self.controller.home_tab.force_reload_faces()
        except Exception:
            pass

    def _add_face_from_image(self):
        from tkinter import filedialog, simpledialog

        path = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[
                ("Image files", "*.jpg;*.jpeg;*.png;*.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        name = simpledialog.askstring("Enroll", "Enter name:")
        if not name:
            return
        img = cv2.imread(path)
        if img is None:
            show_toast("Add face", "Cannot read image")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = self.faces_dir / f"{name}_{ts}.jpg"
        ok = enroll_from_frame(img, name, save_cropped_path=str(out))
        if not ok:
            show_toast("Add face", "Failed to detect/align face")
            return
        show_toast("Add face", f"Saved: {out.name}")
        self._refresh_faces()
        try:
            if hasattr(self.controller, "home_tab") and hasattr(
                self.controller.home_tab, "force_reload_faces"
            ):
                self.controller.home_tab.force_reload_faces()
        except Exception:
            pass

    def _add_face_from_camera(self):
        """
        Enroll face bằng camera đang chạy ở HomeTab (nếu có).
        Reuse EnrollFaceDialog giống Home.
        """
        # Lấy callback frame từ HomeTab nếu controller có home_tab
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
        ok2 = enroll_from_frame(frame_bgr, name, save_cropped_path=str(out))
        if not ok2:
            show_toast("Enroll", "Failed to detect/align face")
            return

        show_toast("Enroll", f"Saved cropped face: {out.name}")
        self._refresh_faces()
        try:
            if hasattr(self.controller, "home_tab") and hasattr(
                self.controller.home_tab, "force_reload_faces"
            ):
                self.controller.home_tab.force_reload_faces()
        except Exception:
            pass

    # ====================== LOGIC: FINGERPRINTS ======================
    def _refresh_fps(self):
        for iid in self.tv_fp.get_children(""):
            self.tv_fp.delete(iid)
        rows = list_fingerprints()
        for r in rows:
            self.tv_fp.insert(
                "", "end", iid=str(r["id"]), values=(r["id"], r.get("name", ""))
            )

    def _auto_refresh_fps(self):
        """Auto-refresh fingerprint list mỗi 5 giây."""
        try:
            self._refresh_fps()
        except Exception:
            pass
        try:
            self.after(5000, self._auto_refresh_fps)
        except Exception:
            pass

    def _fp_add(self):
        from tkinter import simpledialog

        name = simpledialog.askstring("Add fingerprint", "Enter name (optional):") or ""
        try:
            fid = add_fingerprint_placeholder(name)
            show_toast("Fingerprint", f"Added id={fid}")
        except Exception as e:
            show_toast("Fingerprint", f"Error: {e}")
        self._refresh_fps()

    def _fp_delete(self):
        sel = self.tv_fp.selection()
        if not sel:
            return
        fid = int(sel[0])
        try:
            delete_fingerprint(fid)
            show_toast("Fingerprint", "Deleted")
        except Exception as e:
            show_toast("Fingerprint", f"Error: {e}")
        self._refresh_fps()

    def _fp_delete_all(self):
        from tkinter import messagebox
        ans = messagebox.askyesno(
            "Delete ALL fingerprints",
            "⚠️ Delete ALL fingerprint entries in database?"
        )
        if not ans:
            return
        try:
            from services.fingerprint_service import list_fingerprints, delete_fingerprint
            rows = list_fingerprints()
            for r in rows:
                delete_fingerprint(r["id"])
            show_toast("Fingerprint", "All fingerprints deleted.")
        except Exception as e:
            show_toast("Fingerprint", f"Error: {e}")
        self._refresh_fps()


    # ======= Public API for other tabs =======
    def refresh_faces(self):
        self._refresh_faces()

    def refresh_logs(self):
        self._refresh_logs()

    def refresh_fingerprints(self):
        self._refresh_fps()
