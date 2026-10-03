PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    user_name TEXT NOT NULL,
    gender_code INTEGER NOT NULL CHECK (gender_code IN (1, 2))
) WITHOUT ROWID;

CREATE TABLE courses (
    course_id TEXT PRIMARY KEY,
    course_name TEXT NOT NULL,
    difficulty_level TEXT NOT NULL,
    is_advanced INTEGER NOT NULL CHECK (is_advanced IN (0, 1)),
    advanced_label_rule TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE course_fields (
    course_id TEXT NOT NULL,
    field TEXT NOT NULL,
    field_position INTEGER NOT NULL CHECK (field_position >= 0),
    PRIMARY KEY (course_id, field),
    FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE course_prerequisites (
    course_id TEXT NOT NULL,
    prerequisite_course_id TEXT NOT NULL,
    prerequisite_name TEXT NOT NULL,
    prerequisite_position INTEGER NOT NULL CHECK (prerequisite_position >= 0),
    PRIMARY KEY (course_id, prerequisite_course_id),
    FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE course_information (
    course_id TEXT PRIMARY KEY,
    course_category TEXT NOT NULL
        CHECK (json_valid(course_category) AND json_type(course_category) = 'array'),
    detailed_description TEXT NOT NULL,
    is_advanced INTEGER NOT NULL CHECK (is_advanced IN (0, 1)),
    prerequisites TEXT NOT NULL
        CHECK (json_valid(prerequisites) AND json_type(prerequisites) = 'array'),
    course_materials TEXT NOT NULL
        CHECK (json_valid(course_materials) AND json_type(course_materials) = 'array'),
    FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE user_completed_courses (
    user_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    completed_position INTEGER NOT NULL CHECK (completed_position >= 0),
    PRIMARY KEY (user_id, course_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE interactions (
    user_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    comment REAL NOT NULL CHECK (comment >= 0),
    PRIMARY KEY (user_id, course_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE INDEX idx_users_gender
    ON users(gender_code, user_id);

CREATE INDEX idx_courses_advanced
    ON courses(is_advanced, course_id);

CREATE INDEX idx_course_fields_field
    ON course_fields(field, course_id);

CREATE INDEX idx_course_prerequisites_prerequisite
    ON course_prerequisites(prerequisite_course_id, course_id);

CREATE INDEX idx_course_information_advanced
    ON course_information(is_advanced, course_id);

CREATE INDEX idx_completed_courses_course
    ON user_completed_courses(course_id, user_id);

CREATE INDEX idx_interactions_course_user
    ON interactions(course_id, user_id);

CREATE VIEW interaction_details AS
SELECT
    i.user_id,
    u.user_name,
    u.gender_code,
    i.course_id,
    c.course_name,
    c.difficulty_level,
    c.is_advanced,
    i.comment
FROM interactions AS i
JOIN users AS u ON u.user_id = i.user_id
JOIN courses AS c ON c.course_id = i.course_id;
