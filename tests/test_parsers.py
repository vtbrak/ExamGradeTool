from pathlib import Path

import pandas as pd
import pytest

from app.core.parsers import load_raw_export, parse_exam_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_real_raw_sample_splits_by_key_version():
    # intial scores.csv: 190 rows total, Key Version counts A=52, B=43, C=95
    # (confirmed by direct inspection), identity columns stripped for privacy.
    parsed = parse_exam_file(FIXTURES / "round1_round2_raw_sample.csv")

    assert len(parsed.individual_rows) == 52 + 43
    assert len(parsed.group_rows) == 95
    assert parsed.exam_name == "202610 Exam 4"
    assert parsed.semester_code == "202610"
    # every row in this sample has StudentID/CustomID stripped
    assert len(parsed.missing_id_rows) == 190


def test_synthetic_individual_round_detects_duplicate_student():
    parsed = parse_exam_file(FIXTURES / "synthetic_individual_round.csv")

    assert len(parsed.individual_rows) == 5
    assert len(parsed.group_rows) == 0
    assert len(parsed.missing_id_rows) == 0

    duplicates = parsed.duplicate_student_ids["individual"]
    assert set(duplicates.keys()) == {"1000003"}
    assert len(duplicates["1000003"]) == 2
    assert {r.score for r in duplicates["1000003"]} == {44.0, 10.0}


def test_synthetic_group_round_no_duplicates():
    parsed = parse_exam_file(FIXTURES / "synthetic_group_round.csv")

    assert len(parsed.group_rows) == 4
    assert len(parsed.individual_rows) == 0
    assert parsed.duplicate_student_ids["group"] == {}

    ids = {r.student_id for r in parsed.group_rows}
    assert ids == {"1000001", "1000002", "1000004", "1000005"}


def test_excel_input_is_supported(tmp_path):
    df = pd.read_csv(FIXTURES / "synthetic_individual_round.csv")
    xlsx_path = tmp_path / "round1.xlsx"
    df.to_excel(xlsx_path, index=False)

    parsed = parse_exam_file(xlsx_path)

    assert len(parsed.individual_rows) == 5
    assert parsed.exam_name == "Exam 1"


def test_missing_required_column_raises(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("Foo,Bar\n1,2\n")

    with pytest.raises(ValueError, match="missing required column"):
        load_raw_export(bad_csv)


# --- student ID normalization -------------------------------------------------

from app.core.parsers import REQUIRED_COLUMNS, normalize_student_id  # noqa: E402

# Header row of a current Scantron export, confirmed against the instructor's
# headers.xlsx ("RAw" sheet): the nine required columns plus per-question
# Stu/PriKey/Points/Mark columns for 25 questions, which the parser ignores.
SCANTRON_HEADERS = [
    "QuizName", "QuizClass", "FirstName", "LastName", "StudentID", "CustomID",
    "Earned Points", "Possible Points", "PercentCorrect", "QuizCreated",
    "DataExported", "Key Version",
] + [f"{field}{n}" for n in range(1, 26) for field in ("Stu", "PriKey", "Points", "Mark")]


def test_current_scantron_headers_satisfy_the_parser():
    assert len(SCANTRON_HEADERS) == 112
    assert [c for c in REQUIRED_COLUMNS if c not in SCANTRON_HEADERS] == []


# Header row the Exam Scanner app writes: the ZipGrade layout without
# CustomID, which none of the tools use.
SCANNER_HEADERS = [c for c in SCANTRON_HEADERS if c != "CustomID"]


def test_scanner_headers_satisfy_the_parser():
    assert "CustomID" not in REQUIRED_COLUMNS
    assert [c for c in REQUIRED_COLUMNS if c not in SCANNER_HEADERS] == []


def test_file_without_custom_id_column_parses(tmp_path):
    path = tmp_path / "scanner.csv"
    pd.DataFrame(
        [{
            "QuizName": "202630 Exam 1", "QuizClass": "202630", "FirstName": "Ann",
            "LastName": "Alpha", "StudentID": "0123456", "Earned Points": 40,
            "Possible Points": 50, "PercentCorrect": "80.0", "Key Version": "C",
        }]
    ).to_csv(path, index=False)

    parsed = parse_exam_file(path)
    assert [(r.student_id, r.score, r.round) for r in parsed.group_rows] == [
        ("0123456", 40.0, "group")
    ]
    assert parsed.missing_id_rows == []


@pytest.mark.parametrize(
    "value, expected",
    [
        (1234567, "1234567"),
        (1234567.0, "1234567"),  # float column: a blank cell elsewhere widened it
        ("1234567.0", "1234567"),  # the same, already written out as text
        (" 1234567 ", "1234567"),
        # IDs are always 7 digits, so a spreadsheet's dropped leading zero is
        # put back. Without this the same student doesn't match across files.
        (123456, "0123456"),
        ("123456", "0123456"),
        (123456.0, "0123456"),
        ("0123456", "0123456"),  # already padded: unchanged
        # Only a 6-digit ID is padded. Any other length is a problem to report,
        # not something to guess a correction for, so the value is untouched.
        (12345, "12345"),
        ("12345678", "12345678"),
        ("A1234", "A1234"),  # not numeric: kept as given
        (12.5, "12.5"),  # not a whole number: kept as given, not rounded
        ("", None),
        (None, None),
        (float("nan"), None),
    ],
)
def test_normalize_student_id(value, expected):
    assert normalize_student_id(value) == expected


def _scantron_file(tmp_path, suffix):
    import pandas as pd

    base = {
        "QuizName": "Exam 1", "QuizClass": "202610", "CustomID": None,
        "Earned Points": 40, "Possible Points": 50, "Key Version": "A",
    }
    df = pd.DataFrame(
        [
            dict(base, FirstName="Ann", LastName="Alpha", StudentID=1234567),
            dict(base, FirstName="Ben", LastName="Bravo", StudentID=None),
        ]
    )
    path = tmp_path / f"exam{suffix}"
    df.to_excel(path, index=False) if suffix == ".xlsx" else df.to_csv(path, index=False)
    return path


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_one_blank_id_does_not_turn_other_ids_into_decimals(tmp_path, suffix):
    """Regression: a single blank StudentID made pandas read the whole column as
    floats, so 1234567 was saved as "1234567.0" -- a different student from the
    same person's other exams."""
    parsed = parse_exam_file(_scantron_file(tmp_path, suffix))
    ids = [r.student_id for r in parsed.individual_rows + parsed.missing_id_rows]
    assert "1234567" in ids
    assert not any(i and i.endswith(".0") for i in ids)


def test_same_student_matches_whether_or_not_the_zero_was_dropped(tmp_path):
    """The case the padding exists for: a Scantron export keeps 0123456 as text
    while a D2L course export hands over the same ID as the number 123456."""
    import pandas as pd

    from app.core.course_export import parse_course_export

    scantron = tmp_path / "exam.csv"
    pd.DataFrame(
        [{
            "QuizName": "Exam 1", "QuizClass": "202610", "FirstName": "Ann",
            "LastName": "Alpha", "StudentID": "0123456", "CustomID": None,
            "Earned Points": 40, "Possible Points": 50, "Key Version": "A",
        }]
    ).to_csv(scantron, index=False)

    course = tmp_path / "course.xlsx"
    header = "Exam 1 Points Grade <Numeric MaxPoints:80 Category:>"
    pd.DataFrame(
        [{"OrgDefinedId": 123456, "Last Name": "Alpha", "First Name": "Ann", header: 60}]
    ).to_excel(course, index=False)

    from_exam = parse_exam_file(scantron).individual_rows[0].student_id
    from_course = parse_course_export(course).rows[0].student_id
    assert from_exam == from_course == "0123456"


# --- IDs that are reported rather than corrected -------------------------------

from app.core.parsers import clean_student_id  # noqa: E402


@pytest.mark.parametrize(
    "value, expected_id, expected_problem",
    [
        (1234567, "1234567", None),
        (123456, "0123456", None),  # the one padding case
        (123456.0, "0123456", None),  # decimal stripped, then padded
        ("1234567.0", "1234567", None),
        (12345, "12345", "5 digits, expected 7"),
        ("12345678", "12345678", "8 digits, expected 7"),
        ("A1234", "A1234", "not a number"),
        (12.5, "12.5", "not a whole number"),
        ("", None, None),  # blank is "missing", handled separately
        (None, None, None),
    ],
)
def test_clean_student_id_reports_instead_of_guessing(value, expected_id, expected_problem):
    assert clean_student_id(value) == (expected_id, expected_problem)


def test_problem_ids_are_reported_with_their_row_number(tmp_path):
    import pandas as pd

    base = {
        "QuizName": "Exam 1", "QuizClass": "202610", "CustomID": None,
        "Earned Points": 40, "Possible Points": 50, "Key Version": "A",
    }
    path = tmp_path / "exam.csv"
    pd.DataFrame(
        [
            dict(base, FirstName="Ann", LastName="Alpha", StudentID="1234567"),
            dict(base, FirstName="Ben", LastName="Bravo", StudentID="123456"),
            dict(base, FirstName="Cara", LastName="Charlie", StudentID="12345"),
            dict(base, FirstName="Dan", LastName="Delta", StudentID="X9999"),
        ]
    ).to_csv(path, index=False)

    parsed = parse_exam_file(path)
    # Row numbers match the file opened in Excel: header is row 1.
    assert [(p.row, p.student_id, p.problem) for p in parsed.id_problem_rows] == [
        (4, "12345", "5 digits, expected 7"),
        (5, "X9999", "not a number"),
    ]
    # The two good IDs are untouched, including the padded one.
    assert parsed.flagged_student_ids == {"12345", "X9999"}
    assert [r.student_id for r in parsed.individual_rows] == [
        "1234567", "0123456", "12345", "X9999",
    ]


# --- StudentID vs CustomID ----------------------------------------------------

def test_student_id_wins_when_both_ids_are_present(tmp_path):
    """`CustomID` is a fallback for old ZipGrade files, never an override: if a
    row carries both and they disagree, `StudentID` is the one used."""
    base = {
        "QuizName": "Exam 1", "QuizClass": "202610",
        "Earned Points": 40, "Possible Points": 50, "Key Version": "A",
    }
    path = tmp_path / "both_ids.csv"
    pd.DataFrame(
        [
            dict(base, FirstName="Ann", LastName="Both", StudentID="1234567",
                 CustomID="9999999"),
            dict(base, FirstName="Ben", LastName="IdBlank", StudentID="",
                 CustomID="7654321"),
            dict(base, FirstName="Cara", LastName="CustomOnly", StudentID=None,
                 CustomID="123456"),
        ]
    ).to_csv(path, index=False)

    ids = {r.last_name: r.student_id for r in parse_exam_file(path).individual_rows}
    assert ids["Both"] == "1234567"  # not the CustomID
    assert ids["IdBlank"] == "7654321"  # falls back when StudentID is blank
    assert ids["CustomOnly"] == "0123456"  # fallback is padded like any other ID


def test_custom_id_column_is_optional(tmp_path):
    """The Exam Scanner writes no CustomID column at all; that must still parse."""
    path = tmp_path / "no_custom_id.csv"
    pd.DataFrame(
        [{
            "QuizName": "Exam 1", "QuizClass": "202610", "FirstName": "Ann",
            "LastName": "Alpha", "StudentID": "1234567", "Earned Points": 40,
            "Possible Points": 50, "Key Version": "A",
        }]
    ).to_csv(path, index=False)
    assert [r.student_id for r in parse_exam_file(path).individual_rows] == ["1234567"]
