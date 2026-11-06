-- FraudLens – lược đồ PostgreSQL (nguồn sự thật duy nhất)
-- Tự chạy khi container Postgres khởi tạo lần đầu; chạy tay: psql "$DATABASE_URL" -f db/schema.sql

CREATE TABLE IF NOT EXISTS transactions (
    id                BIGSERIAL PRIMARY KEY,
    trans_num         TEXT UNIQUE,                 -- NULL nếu payload lỗi không có mã
    event_ts          BIGINT,                      -- thời gian giao dịch (epoch giây, UTC)
    card_hash         TEXT,                        -- HMAC số thẻ, không lưu số thẻ thật
    card_last4        TEXT,
    merchant          TEXT,
    category          TEXT,
    amt               NUMERIC(14,2),
    city_pop          INTEGER,
    state             TEXT,
    age               REAL,
    distance_km       REAL,
    status            TEXT NOT NULL CHECK (status IN ('VALID','INVALID','SEED')),
    validation_errors JSONB NOT NULL DEFAULT '[]',
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_txn_card_time ON transactions (card_hash, event_ts);
CREATE INDEX IF NOT EXISTS ix_txn_event_ts ON transactions (event_ts);

CREATE TABLE IF NOT EXISTS scores (
    txn_id              BIGINT PRIMARY KEY REFERENCES transactions(id),
    status              TEXT NOT NULL CHECK (status IN ('SCORED','SCORING_FAILED','INVALID','HISTORY_UNAVAILABLE')),
    score_raw           DOUBLE PRECISION,
    score               DOUBLE PRECISION,          -- xác suất sau hiệu chỉnh
    model_version       TEXT,
    feature_set_version TEXT,
    features            JSONB,                     -- để giải thích và truy vết
    error               TEXT,
    latency_ms          REAL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS routing_decisions (
    txn_id          BIGINT PRIMARY KEY REFERENCES transactions(id),
    route           TEXT NOT NULL CHECK (route IN ('LOW','MEDIUM','HIGH','UNSCORED')),
    config_version  TEXT,
    t_medium        DOUBLE PRECISION,
    t_high          DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cases (
    id             BIGSERIAL PRIMARY KEY,
    txn_id         BIGINT UNIQUE NOT NULL REFERENCES transactions(id),
    route          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('OPEN','INVESTIGATING','ESCALATED','CLOSED_FRAUD','CLOSED_LEGIT')),
    assigned_role  TEXT NOT NULL DEFAULT 'analyst',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_cases_queue ON cases (status, route, created_at);

-- Hàng đợi soạn brief (outbox): ghi cùng transaction với hồ sơ nên không thể mất việc
CREATE TABLE IF NOT EXISTS brief_jobs (
    id               BIGSERIAL PRIMARY KEY,
    case_id          BIGINT NOT NULL REFERENCES cases(id),
    status           TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','DONE','FAILED')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at        TIMESTAMPTZ,
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_jobs_pending ON brief_jobs (status, next_attempt_at);

CREATE TABLE IF NOT EXISTS briefs (
    id              BIGSERIAL PRIMARY KEY,
    case_id         BIGINT NOT NULL REFERENCES cases(id),
    brief           JSONB NOT NULL,
    brief_source    TEXT NOT NULL CHECK (brief_source IN ('llm','fallback')),
    llm_model       TEXT,
    prompt_version  TEXT,
    kb_version      TEXT,
    chunk_ids       TEXT[],
    factors         JSONB,
    guardrail       JSONB,
    tokens          INTEGER,
    latency_ms      REAL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_briefs_case ON briefs (case_id, created_at DESC);

CREATE TABLE IF NOT EXISTS case_actions (
    id          BIGSERIAL PRIMARY KEY,
    case_id     BIGINT NOT NULL REFERENCES cases(id),
    actor       TEXT NOT NULL,
    role        TEXT NOT NULL,
    action      TEXT NOT NULL,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Nhãn đến trễ: chỉ dùng để đánh giá và giám sát, không bao giờ tới Scorer hay LLM
CREATE TABLE IF NOT EXISTS labels_eval (
    trans_num    TEXT PRIMARY KEY,
    is_fraud     BOOLEAN NOT NULL,
    label_ts     BIGINT,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS monitor_metrics (
    id            BIGSERIAL PRIMARY KEY,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_start  BIGINT,
    window_end    BIGINT,
    metric        TEXT NOT NULL,
    feature       TEXT,
    value         DOUBLE PRECISION,
    level         TEXT,
    owner         TEXT,                        -- người chịu trách nhiệm khi có cảnh báo
    action        TEXT                         -- việc cần làm khi có cảnh báo
);
-- DB tạo từ bản schema cũ: thêm cột (monitor cũng tự chạy lệnh này khi khởi động)
ALTER TABLE monitor_metrics ADD COLUMN IF NOT EXISTS owner TEXT;
ALTER TABLE monitor_metrics ADD COLUMN IF NOT EXISTS action TEXT;

-- Nhật ký audit chỉ được thêm, mỗi dòng mang băm của dòng trước
CREATE TABLE IF NOT EXISTS audit_log (
    seq        BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    entity     TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    action     TEXT NOT NULL,
    actor      TEXT NOT NULL,
    payload    JSONB NOT NULL,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);

CREATE OR REPLACE FUNCTION audit_log_readonly() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log chỉ được thêm, không được sửa hoặc xóa';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_log;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_readonly();

-- Tài khoản đăng nhập web (mật khẩu băm PBKDF2, phiên lưu băm SHA-256 của token)
CREATE TABLE IF NOT EXISTS app_users (
    id             BIGSERIAL PRIMARY KEY,
    username       TEXT UNIQUE NOT NULL,
    display_name   TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('analyst','risk_lead','admin')),
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS app_sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON app_sessions (user_id);

-- Job vận hành chạy từ giao diện web (simulator, nạp lịch sử...)
CREATE TABLE IF NOT EXISTS ops_jobs (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,
    params        JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'RUNNING'
                  CHECK (status IN ('RUNNING','STOPPING','DONE','FAILED','STOPPED')),
    progress      JSONB,
    result        JSONB,
    error         TEXT,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_ops_jobs_kind ON ops_jobs (kind, id DESC);
