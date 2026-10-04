CREATE TABLE IF NOT EXISTS app_chat_memory (
    chat_session_id TEXT PRIMARY KEY REFERENCES app_chat_sessions(chat_session_id) ON DELETE CASCADE,
    summary_text TEXT NOT NULL DEFAULT '',
    extracted_facts_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extracted_facts_json)),
    memory_version INTEGER NOT NULL DEFAULT 0 CHECK (memory_version >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_agent_runs (
    run_id TEXT PRIMARY KEY,
    chat_session_id TEXT NOT NULL REFERENCES app_chat_sessions(chat_session_id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES app_accounts(account_id) ON DELETE CASCADE,
    trigger_message_id TEXT NOT NULL REFERENCES app_chat_messages(message_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'WAITING_USER', 'COMPLETED', 'FAILED')),
    step_count INTEGER NOT NULL DEFAULT 0 CHECK (step_count >= 0),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT
);

CREATE TABLE IF NOT EXISTS app_agent_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES app_agent_runs(run_id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL CHECK (step_index > 0),
    action_type TEXT NOT NULL,
    tool_name TEXT,
    input_json TEXT NOT NULL CHECK (json_valid(input_json)),
    output_json TEXT NOT NULL CHECK (json_valid(output_json)),
    status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'REJECTED', 'FAILED')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, step_index)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_app_agent_runs_active_session
    ON app_agent_runs(chat_session_id)
    WHERE status IN ('RUNNING', 'WAITING_USER');

CREATE INDEX IF NOT EXISTS idx_app_agent_runs_account
    ON app_agent_runs(account_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_app_agent_steps_run
    ON app_agent_steps(run_id, step_index);
