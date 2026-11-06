# Note from the Exam Scanner app to the Exam Grade Tool

The Exam Scanner app (an iPhone app replacing ZipGrade) will produce the grade CSV that the Exam Grade Tool reads. **The column layout is unchanged:** it is exactly `output headers.csv`, including the required `Earned Points` column. What's new is some of the **values** that can appear in those columns. Please confirm the Exam Grade Tool accepts each of the following, or say what it needs instead.

## Values to confirm

| Column | Value | When it appears | How it's scored |
|---|---|---|---|
| `StuN` | **Two letters, e.g. `AB`** (new) | The question has two correct answers and the student filled in both | Correct: `PointsN` = `2`, `MarkN` = `C` |
| `StuN` | **`BNK`** | The student left the question blank (confirmed in review) | Wrong: `PointsN` = `0`, `MarkN` = `X` |
| `PriKeyN` | **Two letters, e.g. `AB`** | The question has two correct answers; either one earns the points | As usual |
| `MarkN` | **`Drop`** | The question was dropped for everyone | `PointsN` = `2` for every student; `PriKeyN` keeps the original key; `Possible Points` stays `50` |
| `PercentCorrect` | **One decimal place, e.g. `80.0`** | Always (ZipGrade's sample showed `80`) | Information only; the raw score is in `Earned Points` |

Two-letter values are always in alphabetical order (`AB`, never `BA`).

## Other things to know about the file

- **Two rows per student per exam:** one for Form A or B (first attempt) and one for Form C (second attempt, group exam). `Key Version` says which. A student who missed one attempt has only one row.
- **Rows are sorted by last name**, with each student's two rows together.
- **No blank rows** after the data.
- **Every export is the full file.** If late papers are scanned or a fix is made after an export, the next export contains all papers again, not just the changes.
- **Every row is clean:** the app won't export until blanks, double marks, unreadable IDs and form problems are resolved. Papers from students not on the roster are left out.
- **Each question is worth 2 points; 25 questions = `Possible Points` 50.**
- **`QuizName`, `QuizClass`, `QuizCreated`** come from the Exam Maker's mapping file (and can be edited in the app). `QuizClass` is the semester code, e.g. `202630`.
- **`DataExported`** keeps ZipGrade's format, e.g. `9/21/26 17:34`.
- File name: `<QuizName> - Grades - <yyyy-mm-dd>.csv`, e.g. `202630 Exam 1 - Grades - 2026-09-24.csv`.

## Please reply with

1. Whether each value in the table above is accepted as-is.
2. Anything the Exam Grade Tool needs that isn't listed here.
