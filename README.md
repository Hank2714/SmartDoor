# REPO Collaborator
Nguyen Huy Anh, Nguyen Quy Dung, Nguyen Van Hiep


# SmartDoor
Project Đồ Án Cơ Điện Tử 


🚪 SmartDoor – Hệ Thống Mở Cửa Thông Minh
Nhận diện khuôn mặt • Passcode • ESP32 • Tkinter UI • MySQL

📌 Giới thiệu

SmartDoor là hệ thống mở cửa thông minh kết hợp nhận diện khuôn mặt theo thời gian thực, xác thực bằng passcode được mã hoá, và điều khiển chốt cửa bằng ESP32.
Hệ thống hướng tới mục tiêu an toàn – tiện lợi – dễ mở rộng, phù hợp cho nhà ở, văn phòng, và phòng lab.

Hệ thống bao gồm:

* Giao diện Desktop UI hiện đại (Tkinter + ttkbootstrap)

* Nhận diện khuôn mặt bằng MTCNN + DeepFace

* Mã hoá passcode bằng Cryptography/Fernet

* Passcode chính, guest code, mã dùng một lần

* Giao tiếp UART với ESP32 để mở/đóng cửa

* Lưu trữ log & cài đặt bằng MySQL

📁 Cấu trúc thư mục
```
└── 📁Đồ Án
    └── 📁.vscode
        ├── settings.json
    └── 📁db
        └── 📁__pycache__
        ├── __init__.py
        ├── create_table.sql
        ├── db_conn.py
    └── 📁faces
        ├── Ảnh khuôn mặt nhận diện...
    └── 📁services
        └── 📁__pycache__
        ├── camera_daemon.py
        ├── door_controller.py
        ├── face_service.py
        ├── fingerprint_service.py
        ├── log_service.py
        ├── passcode_service.py
        ├── recog_daemon.py
        ├── serial_service.py
        ├── settings_service.py
        ├── vault.py
    └── 📁ui
        └── 📁__pycache__
        ├── home.py
        ├── manage.py
    ├── .env
    ├── app.py
    └── requirements.txt
```

⚙️ Cài đặt & Chạy thử

1️⃣ Clone repository
```
git clone https://github.com/Hank2714/SmartDoor/
cd SmartDoor
```

2️⃣ Cài đặt thư viện cần thiết
```
pip install -r requirements.txt
```

3️⃣ Tạo file .env
```
MYSQL_HOST=localhost
MYSQL_USER=root
MYSQL_PORT=3306
MYSQL_PASS=yourpassword (thay đổi password theo ý bạn)
MYSQL_DB=smartdoor_db

SMARTDOOR_VAULT_KEY=your_fernet_key_here (thay đổi fernet_key theo ý bạn)
SERIAL_PORT=AUTO
SERIAL_BAUD=115200
```
Tạo Fernet key:
```
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

4️⃣ Khởi tạo Database MySQL

```
CREATE DATABASE {your_database_name}
```

5️⃣ Chạy ứng dụng
```
python app.py
```


