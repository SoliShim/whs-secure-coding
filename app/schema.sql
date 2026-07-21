PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    username            TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name        TEXT NOT NULL,
    password_hash       TEXT NOT NULL,
    bio                 TEXT NOT NULL DEFAULT '',
    role                TEXT NOT NULL DEFAULT 'user',
    status              TEXT NOT NULL DEFAULT 'active',
    status_source       TEXT NOT NULL DEFAULT 'none',
    wallet_balance      INTEGER NOT NULL DEFAULT 100000,
    failed_login_count  INTEGER NOT NULL DEFAULT 0,
    locked_until        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK (length(username) BETWEEN 3 AND 30),
    CHECK (username = lower(username)),
    CHECK (length(display_name) BETWEEN 2 AND 30),
    CHECK (length(password_hash) BETWEEN 50 AND 512),
    CHECK (length(bio) <= 500),
    CHECK (role IN ('user', 'admin')),
    CHECK (status IN ('active', 'suspended', 'deleted')),
    CHECK (status_source IN ('none', 'auto_report', 'admin')),
    CHECK (typeof(wallet_balance) = 'integer' AND wallet_balance BETWEEN 0 AND 1000000000000),
    CHECK (typeof(failed_login_count) = 'integer' AND failed_login_count BETWEEN 0 AND 100),
    CHECK (locked_until IS NULL OR length(locked_until) BETWEEN 20 AND 40)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash      TEXT PRIMARY KEY,
    user_id         INTEGER,
    csrf_token      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE CASCADE,
    CHECK (length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(csrf_token) BETWEEN 32 AND 200),
    CHECK (expires_at > created_at),
    CHECK (last_seen_at >= created_at)
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id       INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    price           INTEGER NOT NULL,
    image_filename  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'available',
    is_hidden       INTEGER NOT NULL DEFAULT 0,
    visibility_source TEXT NOT NULL DEFAULT 'none',
    report_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (seller_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK (length(name) BETWEEN 2 AND 80),
    CHECK (length(description) BETWEEN 10 AND 2000),
    CHECK (typeof(price) = 'integer' AND price BETWEEN 1 AND 100000000),
    CHECK (
        length(image_filename) BETWEEN 5 AND 255
        AND instr(image_filename, '/') = 0
        AND instr(image_filename, char(92)) = 0
    ),
    CHECK (status IN ('available', 'reserved', 'sold', 'deleted')),
    CHECK (is_hidden IN (0, 1)),
    CHECK (visibility_source IN ('none', 'auto_report', 'admin')),
    CHECK (typeof(report_count) = 'integer' AND report_count BETWEEN 0 AND 1000000)
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id       INTEGER NOT NULL,
    recipient_id    INTEGER,
    body            TEXT NOT NULL,
    client_nonce    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (recipient_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    UNIQUE (sender_id, client_nonce),
    CHECK (length(body) BETWEEN 1 AND 500),
    CHECK (length(client_nonce) BETWEEN 16 AND 200),
    CHECK (recipient_id IS NULL OR recipient_id != sender_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id     INTEGER NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    auto_eligible   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    resolved_at     TEXT,
    resolved_by     INTEGER,
    FOREIGN KEY (reporter_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (resolved_by) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    UNIQUE (reporter_id, target_type, target_id),
    CHECK (target_type IN ('user', 'product')),
    CHECK (typeof(target_id) = 'integer' AND target_id > 0),
    CHECK (length(reason) BETWEEN 1 AND 500),
    CHECK (status IN ('pending', 'actioned', 'dismissed')),
    CHECK (auto_eligible IN (0, 1)),
    CHECK (
        (status = 'pending' AND resolved_at IS NULL AND resolved_by IS NULL)
        OR status IN ('actioned', 'dismissed')
    )
);

CREATE TABLE IF NOT EXISTS transfers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id           INTEGER NOT NULL,
    recipient_id        INTEGER NOT NULL,
    amount              INTEGER NOT NULL,
    memo                TEXT NOT NULL DEFAULT '',
    idempotency_key     TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (sender_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (recipient_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    UNIQUE (sender_id, idempotency_key),
    CHECK (sender_id != recipient_id),
    CHECK (typeof(amount) = 'integer' AND amount BETWEEN 1 AND 1000000),
    CHECK (length(memo) <= 100),
    CHECK (
        length(idempotency_key) BETWEEN 32 AND 200
        AND idempotency_key NOT GLOB '*[^A-Za-z0-9_-]*'
    )
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id     INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    delta           INTEGER NOT NULL,
    balance_after   INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (transfer_id) REFERENCES transfers(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    UNIQUE (transfer_id, user_id),
    CHECK (typeof(delta) = 'integer' AND delta != 0 AND delta BETWEEN -1000000 AND 1000000),
    CHECK (typeof(balance_after) = 'integer' AND balance_after BETWEEN 0 AND 1000000000000)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id   INTEGER,
    event_type      TEXT NOT NULL,
    target_type     TEXT,
    target_id       INTEGER,
    metadata        TEXT NOT NULL DEFAULT '{}',
    ip_hash         TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (actor_user_id) REFERENCES users(id) ON UPDATE RESTRICT ON DELETE SET NULL,
    CHECK (length(event_type) BETWEEN 3 AND 100),
    CHECK (target_type IS NULL OR length(target_type) BETWEEN 1 AND 50),
    CHECK (target_id IS NULL OR (typeof(target_id) = 'integer' AND target_id > 0)),
    CHECK (length(metadata) <= 4000),
    CHECK (
        ip_hash IS NULL
        OR (length(ip_hash) = 64 AND ip_hash NOT GLOB '*[^0-9a-f]*')
    )
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_products_visible ON products(is_hidden, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_products_seller ON products(seller_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_global ON messages(id DESC) WHERE recipient_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_messages_recipient ON messages(recipient_id, sender_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_type, target_id, status);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_transfers_sender ON transfers(sender_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_transfers_recipient ON transfers(recipient_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger_entries(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_user_id, id DESC);

CREATE TRIGGER IF NOT EXISTS ledger_entries_no_update
BEFORE UPDATE ON ledger_entries
BEGIN
    SELECT RAISE(ABORT, 'ledger entries are immutable');
END;

CREATE TRIGGER IF NOT EXISTS ledger_entries_no_delete
BEFORE DELETE ON ledger_entries
BEGIN
    SELECT RAISE(ABORT, 'ledger entries are immutable');
END;

CREATE TRIGGER IF NOT EXISTS transfers_no_update
BEFORE UPDATE ON transfers
BEGIN
    SELECT RAISE(ABORT, 'transfers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS transfers_no_delete
BEFORE DELETE ON transfers
BEGIN
    SELECT RAISE(ABORT, 'transfers are immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_logs_no_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit logs are append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit logs are append-only');
END;

PRAGMA user_version = 3;
