CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_accounts (
    account_id TEXT PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_profiles (
    account_id TEXT PRIMARY KEY REFERENCES app_accounts(account_id) ON DELETE CASCADE,
    user_id TEXT UNIQUE REFERENCES users(user_id),
    display_name TEXT,
    gender_code INTEGER CHECK (gender_code IN (1, 2)),
    profile_version INTEGER NOT NULL DEFAULT 0 CHECK (profile_version >= 0),
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'CONFIRMED')),
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS app_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES app_accounts(account_id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_chat_sessions (
    chat_session_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES app_accounts(account_id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose = 'RECOMMENDATION'),
    state TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    draft_display_name TEXT,
    draft_gender_code INTEGER CHECK (draft_gender_code IN (1, 2)),
    event_seq INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_chat_session_courses (
    chat_session_id TEXT NOT NULL REFERENCES app_chat_sessions(chat_session_id) ON DELETE CASCADE,
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    course_position INTEGER NOT NULL CHECK (course_position >= 0),
    PRIMARY KEY (chat_session_id, course_id)
);

CREATE TABLE IF NOT EXISTS app_chat_messages (
    message_id TEXT PRIMARY KEY,
    chat_session_id TEXT NOT NULL REFERENCES app_chat_sessions(chat_session_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    client_message_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_course_resolutions (
    resolution_id TEXT PRIMARY KEY,
    chat_session_id TEXT NOT NULL REFERENCES app_chat_sessions(chat_session_id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES app_accounts(account_id) ON DELETE CASCADE,
    query_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'CONFIRMED', 'REJECTED', 'EXPIRED')),
    selected_course_id TEXT REFERENCES courses(course_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_resolution_candidates (
    resolution_id TEXT NOT NULL REFERENCES app_course_resolutions(resolution_id) ON DELETE CASCADE,
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    candidate_rank INTEGER NOT NULL CHECK (candidate_rank > 0),
    match_score REAL NOT NULL,
    match_reason TEXT NOT NULL,
    PRIMARY KEY (resolution_id, course_id),
    UNIQUE (resolution_id, candidate_rank)
);

CREATE TABLE IF NOT EXISTS app_recommendations (
    recommendation_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES app_accounts(account_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    profile_version INTEGER NOT NULL,
    source TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    fairness_applied INTEGER NOT NULL CHECK (fairness_applied IN (0, 1)),
    fairness_policy_version TEXT,
    generated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS app_recommendation_items (
    recommendation_id TEXT NOT NULL REFERENCES app_recommendations(recommendation_id) ON DELETE CASCADE,
    rank INTEGER NOT NULL CHECK (rank > 0),
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    course_snapshot_json TEXT NOT NULL CHECK (json_valid(course_snapshot_json)),
    reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json)),
    reason_text TEXT NOT NULL,
    PRIMARY KEY (recommendation_id, rank),
    UNIQUE (recommendation_id, course_id)
);

CREATE TABLE IF NOT EXISTS app_favorites (
    account_id TEXT NOT NULL REFERENCES app_accounts(account_id) ON DELETE CASCADE,
    course_id TEXT NOT NULL REFERENCES courses(course_id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (account_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_app_refresh_tokens_account ON app_refresh_tokens(account_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_app_chat_sessions_account ON app_chat_sessions(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_chat_messages_session ON app_chat_messages(chat_session_id, created_at, message_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_chat_messages_client_id
    ON app_chat_messages(chat_session_id, client_message_id)
    WHERE client_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_app_resolutions_session ON app_course_resolutions(chat_session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_app_recommendations_account ON app_recommendations(account_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_favorites_account ON app_favorites(account_id, created_at DESC);
