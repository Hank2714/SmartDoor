# app.py
from __future__ import annotations
import ttkbootstrap as tb
from ttkbootstrap.constants import *

from ui.home import HomeTab
from ui.manage import ManageTab
from services.door_controller import DoorController


class App(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly")
        self.title("Smart Door")
        # Mở full màn hình (zoomed) nhưng vẫn cho resize nếu muốn
        self.state("zoomed")
        # Nếu muốn cho resize thì để True, True
        self.resizable(True, True)

        # ---- Door controller (ESP32) ----
        # App giữ một instance DoorController, HomeTab dùng cái này.
        self.door = DoorController()

        # ---- Notebook với 2 tab: Home + Manage ----
        nb = tb.Notebook(self)
        nb.pack(fill=BOTH, expand=YES)

        # HomeTab nhận controller = DoorController (để gửi "open manual", "close", set_hold_time, v.v.)
        self.home_tab = HomeTab(nb, controller=self.door)
        nb.add(self.home_tab, text="Home")

        # ManageTab nhận controller = App (để có thể gọi controller.home_tab.force_reload_faces())
        self.manage_tab = ManageTab(nb, controller=self)
        nb.add(self.manage_tab, text="Manage")

        # Để tab khác (nếu sau này thêm) cũng truy cập được
        # ví dụ: self.manage_tab.refresh_logs(), self.manage_tab.refresh_faces(), ...
        # hoặc HomeTab có thể gọi self.master.manage_tab.refresh_logs() nếu cần.

        # Bắt sự kiện đóng cửa sổ để tắt serial/camera/daemon gọn gàng
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- Clean up khi thoát app ----
    def _on_close(self):
        try:
            # HomeTab.destroy() đã stop camera + recog daemon
            if hasattr(self, "home_tab") and self.home_tab:
                try:
                    self.home_tab.destroy()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            # Đóng serial tới ESP32
            if hasattr(self, "door") and self.door:
                self.door.shutdown()
        except Exception:
            pass

        # Đóng luôn cửa sổ chính
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
