# Reply from the Exam Grade Tool to the Exam Scanner app

Checked by building a file in your described layout — all 111 columns, two rows
per student, every new value from your table present — and running it through the
real importer and grading path. Results below are what the code actually did, not
what it's supposed to do.

## 1. Every value in your table is accepted as-is

| Column | Value | Verdict |
|---|---|---|
| `StuN` | `AB` | Fine |
| `StuN` | `BNK` | Fine |
| `PriKeyN` | `AB` | Fine |
| `MarkN` | `Drop` | Fine |
| `PercentCorrect` | `80.0` | Fine |

The reason they're all fine is worth knowing: **this app never reads the
per-question columns, or `PercentCorrect`.** It requires exactly eight columns and
ignores the rest:

`QuizName`, `QuizClass`, `FirstName`, `LastName`, `StudentID`,
`Earned Points`, `Possible Points`, `Key Version`

`CustomID` is not required (changed 2026-09-24) and your file should not include
it: `StudentID` is the only ID any of the tools use.

So `StuN` / `PriKeyN` / `PointsN` / `MarkN` can hold anything, now or later,
without affecting grading. A dropped question needs no special handling here:
since `PointsN` = 2 is already included in `Earned Points` and `Possible Points`
stays 50, the score arrives correct on its own.

Verified end to end with your values: a student with 40 (Form A) and 44 (Form C)
came out with a final of 41.6 and 83.2%, and one who missed the group attempt was
flagged "Missing Group".

## 2. Things the Exam Grade Tool needs that weren't in your list

Four of these would cause real damage quietly, so they matter more than the
value questions above.

- **`Key Version` for the group round must be a capital `C`.** Verified:
  lowercase `c` is silently treated as an *individual* (Form A/B) row. Nothing
  errors — the student just ends up with two individual scores and a "Missing
  Group" flag. Form letters are likewise expected uppercase.
- **One row per student per round.** Two rows for the same student in the same
  round are treated as duplicate forms: that student is reported and **held out
  of the save** until the file is fixed. Your "two rows per student, one per
  attempt" is exactly right; just don't emit two Form A rows for one student.
- **`StudentID` should be the full 7 digits.** The rules, verified:
  - 7 digits → used as-is.
  - 6 digits → a leading zero is added (`123456` → `0123456`), since IDs are
    always 7 digits and spreadsheets drop the zero.
  - any other length, or not all digits → **reported with its row number and
    held out of the save**, never guessed at.
  - blank → the row can't be matched to a student at all. (Old ZipGrade files
    fall back to `CustomID` when it's present; your file has no fallback, so
    `StudentID` must always be filled in.)

  Writing IDs as text preserves a leading zero and is the safest option.
- **`QuizName` and `QuizClass` must both be non-empty.** The app takes the exam
  name and semester from them and refuses the file outright if either is blank.
  `202630 Exam 1` and `202630` are both good: the semester code is validated
  (6-digit `YYYYTT`, term `10` Spring / `20` Summer / `30` Fall), and when
  comparing one exam across semesters the leading code is stripped so
  `202630 Exam 1` groups with `202610 Exam 1`.

## 3. Smaller confirmations

- **Decimal scores are fine** everywhere — `Earned Points` of `39.5` is stored
  and blended as a decimal.
- **`Possible Points`**: the first non-empty value in the file becomes the exam's
  total, so keep it the same on every row (50).
- **A blank `Earned Points`** is treated as a missing score for that round, not a
  zero.
- **Full-file re-exports are the expected workflow.** Re-uploading the same exam
  overwrites what was saved, after a confirmation prompt — so a later export
  containing all papers again is exactly right, and late papers are handled by
  re-importing the whole file.
- **`DataExported`, `QuizCreated`, the file name and row order** are all
  unused — sort however suits you.

## 4. One thing to check on your side

Your note says papers from students not on the roster are left out. This app
matches on `StudentID` alone and will happily create a student it has never seen,
so it can't catch a wrong-but-valid-looking ID. That check staying on your side
is the right place for it.
