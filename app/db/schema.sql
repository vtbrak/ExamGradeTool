CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    first_name TEXT,
    last_name TEXT
);

CREATE TABLE IF NOT EXISTS course_offerings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    semester_code TEXT NOT NULL,
    course_number TEXT NOT NULL,
    -- When the instructor closed the semester, i.e. said its grades are
    -- complete. Null means still in progress. Deliberately explicit rather
    -- than inferred from whether course_grades rows exist: Historical Input's
    -- course-grade and exam-backfill checkboxes are independent, so a semester
    -- can hold course grades while its exam rows are still scanned ones.
    -- Closing is a label, never a lock -- a closed semester can still be
    -- re-imported over.
    closed_at TEXT,
    UNIQUE (semester_code, course_number)
);

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_offering_id INTEGER NOT NULL REFERENCES course_offerings (id),
    exam_name TEXT NOT NULL,
    possible_points REAL,
    UNIQUE (course_offering_id, exam_name)
);

-- The current semester's scanned exam scores: working data, produced by the
-- app from a Scantron file, edited and exported through the Exam Grading
-- section. Never the class record -- that is historical_exam_scores below.
CREATE TABLE IF NOT EXISTS exam_scores (
    exam_id INTEGER NOT NULL REFERENCES exams (id),
    student_id TEXT NOT NULL REFERENCES students (student_id),
    individual_score REAL,
    raw_group_score REAL,
    effective_group_score REAL,
    final_grade REAL,
    individual_key_used TEXT,
    missing_flag TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (exam_id, student_id)
);

-- The class record's exam scores: what a course sheet stated for each exam.
-- Deliberately a different table from exam_scores rather than a flag on it.
-- exam_scores is the current semester's scanned working data, reached only
-- through the Exam Grading section; this is the record. Keeping them apart
-- structurally means no query can conflate them by forgetting a filter, and a
-- scan can never overwrite the record.
--
-- possible_points is stored here rather than taken from `exams`: the sheet
-- states its own MaxPoints, and that is the denominator the record's
-- percentages must use. The scanned side keeps using exams.possible_points,
-- which is what its file stated.
CREATE TABLE IF NOT EXISTS historical_exam_scores (
    exam_id INTEGER NOT NULL REFERENCES exams (id),
    student_id TEXT NOT NULL REFERENCES students (student_id),
    individual_score REAL,
    raw_group_score REAL,
    final_grade REAL,
    possible_points REAL,
    source_file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (exam_id, student_id)
);

-- Overall course grades (lab + exams + everything else), imported from the D2L
-- course-grade export. Deliberately separate from exam_scores: that table is
-- exam-level, this one is the semester-level outcome. The numerator/denominator
-- pair is stored rather than just a percent because D2L drops items a student
-- was exempted from, so the denominator varies student to student.
CREATE TABLE IF NOT EXISTS course_grades (
    course_offering_id INTEGER NOT NULL REFERENCES course_offerings (id),
    student_id TEXT NOT NULL REFERENCES students (student_id),
    adjusted_numerator REAL,
    adjusted_denominator REAL,
    calculated_numerator REAL,
    calculated_denominator REAL,
    -- Whichever pair the sheet actually carried ("adjusted", or "calculated"
    -- for the oldest exports, which have no adjusted columns at all), and the
    -- percentage derived from that pair.
    grade_basis TEXT,
    final_percent REAL,
    -- The parts behind the total, so a student's record can be read item by
    -- item: the lab, any extra credit earned, and everything else (quizzes,
    -- muddiest points, participation and the rest) rolled into one bin.
    -- "possible" counts only items the student has a number for -- an Exempt
    -- cell is excused and a blank states nothing.
    lab_earned REAL,
    lab_possible REAL,
    extra_credit_earned REAL,
    extra_credit_possible REAL,
    other_earned REAL,
    other_possible REAL,
    letter_grade TEXT,
    source_file TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (course_offering_id, student_id)
);

-- The primary keys above lead with the exam / offering, so a lookup by student
-- alone (the Rec Letter detail, student search, duplicate merging) would scan
-- the whole table without these.
CREATE INDEX IF NOT EXISTS exam_scores_by_student ON exam_scores (student_id);
CREATE INDEX IF NOT EXISTS historical_exam_scores_by_student ON historical_exam_scores (student_id);
CREATE INDEX IF NOT EXISTS course_grades_by_student ON course_grades (student_id);
