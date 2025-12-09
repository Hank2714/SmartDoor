CREATE DATABASE IF NOT EXISTS smartdoor_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smartdoor_db;

-- 1) SETTINGS
CREATE TABLE IF NOT EXISTS settings (
  id INT PRIMARY KEY AUTO_INCREMENT,
  door_state ENUM('open','close') DEFAULT 'close',
  hold_time INT DEFAULT 5,
  face_recognition_enabled TINYINT(1) DEFAULT 1,
  fingerprint_enabled TINYINT(1) DEFAULT 1,
  passcode_enabled TINYINT(1) DEFAULT 1,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
INSERT IGNORE INTO settings(id) VALUES (1);

-- 2) PASSCODES
CREATE TABLE IF NOT EXISTS passcodes (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  code_hash VARCHAR(64) NOT NULL,          -- SHA-256 hex
  code_masked VARCHAR(32) DEFAULT NULL,    -- ****-1234
  is_main TINYINT(1) DEFAULT 0,
  is_one_time TINYINT(1) DEFAULT 0,
  valid_from DATETIME DEFAULT CURRENT_TIMESTAMP,
  valid_until DATETIME NULL,               -- (cũ) có thể NULL
  used TINYINT(1) DEFAULT 0,               -- cho mã 1 lần
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  -- code_enc sẽ được thêm bằng block PREPARE phía dưới nếu chưa có
  INDEX idx_passcodes_lookup (is_main, is_one_time, valid_from, valid_until, used)
);

-- Thêm cột code_enc nếu CHƯA tồn tại (tương thích MySQL 5.7/8.0, MariaDB)
SET @stmt := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME   = 'passcodes'
        AND COLUMN_NAME  = 'code_enc'
    ),
    'SELECT 1',  -- đã có, không làm gì
    'ALTER TABLE passcodes ADD COLUMN code_enc LONGBLOB NULL'  -- thêm mới
  )
);
PREPARE s FROM @stmt; EXECUTE s; DEALLOCATE PREPARE s;

-- 🚀 MIGRATION: ép guest passcode có hạn tối thiểu 60 phút nếu đang NULL
UPDATE passcodes
SET valid_until = COALESCE(
        DATE_ADD(created_at, INTERVAL 60 MINUTE),
        DATE_ADD(NOW(),       INTERVAL 60 MINUTE)
    )
WHERE is_main = 0
  AND valid_until IS NULL
  AND used = 0;

-- 3) ACCESS LOG
CREATE TABLE IF NOT EXISTS access_log (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  method ENUM('face','fingerprint','passcode','manual') NOT NULL,
  result ENUM('granted','denied') NOT NULL,
  passcode_masked VARCHAR(32) NULL,
  passcode_hash VARCHAR(64) NULL,
  confidence FLOAT NULL,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4) FACE DATA
CREATE TABLE IF NOT EXISTS face_data (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(128) NULL,
  encoding LONGBLOB NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5) FINGERPRINT DATA
CREATE TABLE IF NOT EXISTS fingerprint_data (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(128) NULL,
  template LONGBLOB NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
