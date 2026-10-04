CREATE TABLE IF NOT EXISTS admin_accounts (
    admin_id TEXT PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin' CHECK (role = 'admin'),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED', 'LOCKED')),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS admin_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL REFERENCES admin_accounts(admin_id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit_logs (
    audit_id TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL REFERENCES admin_accounts(admin_id),
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    result TEXT NOT NULL CHECK (result IN ('SUCCESS', 'FAILURE')),
    summary_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(summary_json)),
    request_id TEXT,
    priority TEXT NOT NULL DEFAULT 'NORMAL' CHECK (priority IN ('NORMAL', 'HIGH')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_course_records (
    course_id TEXT PRIMARY KEY REFERENCES courses(course_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    catalog_version TEXT NOT NULL,
    created_by TEXT REFERENCES admin_accounts(admin_id),
    updated_by TEXT REFERENCES admin_accounts(admin_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_catalog_versions (
    catalog_version TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    created_by TEXT REFERENCES admin_accounts(admin_id),
    summary_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(summary_json)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_aliases (
    alias_id TEXT PRIMARY KEY,
    alias_text TEXT NOT NULL,
    normalized_alias TEXT NOT NULL UNIQUE,
    course_id TEXT NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'ADMIN',
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DELETED')),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_by TEXT REFERENCES admin_accounts(admin_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_user_status (
    account_id TEXT PRIMARY KEY REFERENCES app_accounts(account_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    updated_by TEXT REFERENCES admin_accounts(admin_id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resolution_reviews (
    review_id TEXT PRIMARY KEY,
    resolution_id TEXT NOT NULL UNIQUE REFERENCES app_course_resolutions(resolution_id) ON DELETE CASCADE,
    conclusion TEXT NOT NULL CHECK (conclusion IN ('CORRECT', 'INCORRECT', 'UNCERTAIN')),
    expected_course_id TEXT REFERENCES courses(course_id),
    note TEXT,
    reviewed_by TEXT NOT NULL REFERENCES admin_accounts(admin_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_imports (
    import_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_format TEXT NOT NULL CHECK (file_format IN ('CSV', 'JSONL')),
    base_catalog_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('VALIDATING', 'VALIDATED', 'BLOCKED', 'COMMITTED', 'CANCELLED')),
    content_text TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(summary_json)),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_by TEXT NOT NULL REFERENCES admin_accounts(admin_id),
    created_at TEXT NOT NULL,
    committed_at TEXT
);

CREATE TABLE IF NOT EXISTS course_import_errors (
    error_id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL REFERENCES course_imports(import_id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    field_name TEXT,
    error_code TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS background_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    parameters_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(parameters_json)),
    result_json TEXT CHECK (result_json IS NULL OR json_valid(result_json)),
    error_code TEXT,
    error_summary TEXT,
    created_by TEXT NOT NULL REFERENCES admin_accounts(admin_id),
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS hyperparameter_experiments (
    experiment_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES background_jobs(job_id),
    name TEXT NOT NULL,
    description TEXT,
    mode TEXT NOT NULL CHECK (mode IN ('SINGLE', 'GRID')),
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED')),
    dataset_version TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    evaluation_set_version TEXT NOT NULL,
    reference_queue_version TEXT NOT NULL,
    random_seed INTEGER NOT NULL,
    baseline_experiment_id TEXT REFERENCES hyperparameter_experiments(experiment_id),
    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json)),
    combination_count INTEGER NOT NULL CHECK (combination_count > 0),
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    created_by TEXT NOT NULL REFERENCES admin_accounts(admin_id),
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_hyperparameter_experiments_idempotency
    ON hyperparameter_experiments(created_by, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS hyperparameter_results (
    result_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES hyperparameter_experiments(experiment_id) ON DELETE CASCADE,
    result_status TEXT NOT NULL CHECK (result_status IN ('SUCCEEDED', 'FAILED')),
    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json)),
    metrics_json TEXT CHECK (metrics_json IS NULL OR json_valid(metrics_json)),
    baseline_delta_json TEXT CHECK (baseline_delta_json IS NULL OR json_valid(baseline_delta_json)),
    is_pareto_optimal INTEGER NOT NULL DEFAULT 0 CHECK (is_pareto_optimal IN (0, 1)),
    error_json TEXT CHECK (error_json IS NULL OR json_valid(error_json)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hyperparameter_exports (
    export_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES hyperparameter_experiments(experiment_id) ON DELETE CASCADE,
    format TEXT NOT NULL CHECK (format IN ('CSV', 'JSON')),
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'SUCCEEDED', 'FAILED')),
    content_text TEXT,
    created_by TEXT NOT NULL REFERENCES admin_accounts(admin_id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fairness_policy_snapshots (
    policy_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'READY', 'ACTIVE', 'RETIRED', 'FAILED')),
    algorithm_version TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    reference_queue_version TEXT NOT NULL,
    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json)),
    metrics_json TEXT NOT NULL CHECK (json_valid(metrics_json)),
    source_result_id TEXT REFERENCES hyperparameter_results(result_id),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version > 0),
    created_by TEXT NOT NULL REFERENCES admin_accounts(admin_id),
    activated_by TEXT REFERENCES admin_accounts(admin_id),
    created_at TEXT NOT NULL,
    activated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fairness_policy_single_active
    ON fairness_policy_snapshots(status)
    WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_course_status ON admin_course_records(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_course_alias_course ON course_aliases(course_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON background_jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_course_imports_status ON course_imports(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON hyperparameter_experiments(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_results_experiment ON hyperparameter_results(experiment_id, created_at);
