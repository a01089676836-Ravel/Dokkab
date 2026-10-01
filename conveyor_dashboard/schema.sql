-- Aiven MySQL: 발생 시각과 수신 시각을 분리합니다.
-- event_date / event_time은 한국 시각(UTC+09:00)입니다.
CREATE TABLE IF NOT EXISTS conveyor_event_logs (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    event_id CHAR(36) NOT NULL,
    device_id VARCHAR(100) NOT NULL,
    occurred_at_utc DATETIME(3) NOT NULL,
    event_date DATE NOT NULL,
    event_time TIME(3) NOT NULL,
    severity ENUM('NORMAL', 'WARNING', 'DANGER', 'ERROR') NOT NULL,
    event_description TEXT NOT NULL,
    equipment_stopped BOOLEAN NULL,
    stop_reason TEXT NULL,
    received_at_utc DATETIME(3) NOT NULL,
    UNIQUE KEY uq_device_event (device_id, event_id),
    KEY idx_occurred (occurred_at_utc, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
