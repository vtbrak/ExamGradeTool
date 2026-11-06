# Cell Biology Exam Grader — Roadmap

## Where things stand — v1.1 beta (November 2026)

**This copy is version 1.1, beta.** The version lives in `app/version.py` and
nowhere else; it shows in the window title and the launcher prints it, so a
screenshot or a question about behaviour says which copy it came from. Beta
because the app is in real use but still changing in response to that use.
The matching git tag is `v1.1-beta` on `github.com/vtbrak/ExamGradeTool`
(private).

The app is built and in use. **Items 1–8, 14 and 15 are done.** Items 9, 10, 11
and 12 are **retired — not needed** (see that section: course data is imported
after the semester ends, so the app never writes into a D2L sheet). Item 13 is
the only idea still open, and most of what it covered for a mis-filed sheet is
now built. 528 tests pass.

**The live database is the production copy's business, and that is settled.**
Development works on the generated databases (`tools/generate_dev_data.py`,
`dev-data/`), which are enough; the `.dev-copy` marker keeps this copy off the
live file. The live `grades.db` starts fresh, so there is no legacy data to
migrate and no backlog of duplicates to clean — the earlier follow-ups asking
for a copy of the real file are closed on that basis, not because they were
done.

**The 20-sheet historical backlog is imported** (2026-11-03), so the class
record now holds every semester from Fall 2016. Two real exams have been scanned
through as well. What that leaves:

1. Validate semester codes on the Scantron path (see Open follow-ups) — a small
   gap, and the only one that needs no data to fix.
2. Item 13's per-row removal: taking out one bad score or one bad student row.
   A mis-filed sheet is already covered by Delete Semester.

**The duplicate check and the ID check are closed, not pending** (2026-11-04):
the instructor is not running either, and the IDs came through the 20 imports
clean. Item 8's tool stays in `tools/` unused.

**What the app does.** Two jobs, which the window keeps apart: the label above
the tab bar reads **EXAM** or **COURSE GRADES**, and the tab bar is divided by a
rule between them.

*Exam grading — this semester's scanned exams:*

- **Results** — drop a Scantron exam export (CSV or Excel) and press Run.
  Individual and Group rounds are blended with the 60/40 formula, and duplicate
  forms, missing scores and bad student IDs are flagged. Scores are editable,
  with Recalculate, and every exam score shows raw points **and** percentage.
  Save the scores, reopen a saved exam, or export a CSV ready to paste into the
  D2L gradebook.
- **Dashboard** — letter-grade distribution for the current exam, F to A left to
  right, with the counts in a table beneath and both exportable.

*The class record — past semesters, imported when each one ends:*

- **Class History** — per-semester roster with overall course percentage,
  pass/fail and pass rate. **Import Course Sheet…** brings a semester in;
  **Delete Semester…** removes one outright so it can be re-imported; **Back Up
  Now** and **Restore from Backup…** manage dated copies beside `grades.db`.
- **Reports** — seven charts over the record (course averages and exam averages,
  by semester, by exam, one exam across semesters, and pooled), each with a
  statistics table, a red 70% pass line and a three-file export.
- **Rec Letter Info** — the score needed for the top 0.1 / 1 / 5 / 10 / 20% of
  all past course grades, the same scale for exam averages, and a student search
  showing each semester's course and exam standing.

**How to run it.** Double-click `run_app.command` in Finder. On a Mac where the
app hasn't been set up yet — or after copying this folder from another Mac — it
builds its own Python environment (`venv/`) from `requirements.txt` first. That
needs an internet connection and takes a few minutes; after that it starts
straight away. `./run_app.command --check` reports whether setup is needed
without installing anything or opening the app. Any Python 3.9–3.13 works,
including the one built into macOS. There is no installer or DMG any more
(see "Installer removed" below).

**Copying this folder.** The folder is self-contained and can live anywhere —
including somewhere this project's development copy can't see. Copy it, then
double-click `run_app.command`. Verified from a copy: the launcher, the full
test suite, the duplicate-cleanup tool and the app window all work unchanged.

- **With `venv/` included** (~1.3 GB): works immediately on this Mac.
- **Without `venv/`** (~4 MB): the first launch rebuilds it from
  `requirements.txt`, which needs an internet connection and a few minutes.
  This is also what happens on a different Mac, where the copied `venv/`
  wouldn't run anyway. `./run_app.command --check` says which state a copy is
  in without installing anything.

**Two files must NOT be copied to a production folder:** `.dev-copy` and
`dev-data/`. They are what mark a copy as a development one, and a production
copy carrying them would silently use its own empty database instead of the
course folder's (see "Keeping development work off the live database"). They're
in `.gitignore`, but `cp -R` and a bare `rsync` will both carry them across.

**Updating the production folder** from this one, with those exclusions:

```bash
DEV="$HOME/Desktop/ExamGradeTool"
PROD="$HOME/Desktop/ExamGradeTool-Production"   # change to your folder

# 1. back up the live database first
cp "$HOME/Library/CloudStorage/Dropbox/_0 West Chester/0_Teaching/211 - Cell Biology/01 - Exam tool/grades.db" \
   "$HOME/Desktop/grades_backup_$(date +%Y%m%d).db"

# 2. move the old copy aside, bring the new code in, reuse its venv
mv "$PROD" "$PROD-old-$(date +%Y%m%d)"
rsync -a --exclude venv/ --exclude .dev-copy --exclude dev-data/ \
         --exclude sample_data/ --exclude .pytest_cache/ --exclude __pycache__/ \
         "$DEV"/ "$PROD"/
mv "$PROD-old-$(date +%Y%m%d)/venv" "$PROD/venv"

# 3. check before trusting it -- the second line must NOT say DEVELOPMENT
cd "$PROD" && ./run_app.command --check && \
  QT_QPA_PLATFORM=offscreen ./venv/bin/python -m pytest tests/ -q
```

Moving the old folder aside rather than copying over the top leaves a complete
rollback and clears out anything stale. Keep the `-old-` folder until satisfied,
then trash it — checking it first for anything of your own.

**Confirm which database a copy will use.** `run_app.command` prints it before
the window opens, and the window title says `— DEVELOPMENT DATA` when it isn't
the live one. In a production copy that line should name the course folder.

Two things are deliberately *not* inside the folder, and are therefore shared by
every production copy on this Mac:

- **The database.** `grades.db` lives in the Dropbox course folder, so every
  production copy reads and writes the same grade history. Copying the folder
  does not copy or fork the data. (A development copy is the exception — that is
  the point of `.dev-copy`.)
- **The data-folder setting.** File → Choose Data Folder… is saved per user
  under the user's Library folder (see `app/db/config.py`). Changing it in one
  production copy changes it for all of them. A development copy keeps its own.

**Where things live.**

- **Code:** `~/Desktop/ExamGradeTool/` (kept off Dropbox to avoid sync conflicts
  during development).
  - `app/core/` — parsing, grading, semester codes
  - `app/db/` — database schema and queries
  - `app/gui/` — the window and its tabs
  - `tests/` — test suite; run with `venv/bin/python -m pytest tests/`
  - `tools/` — `duplicate_students.py`, the one-off duplicate cleanup (item 8)
  - `sample_data/` — the four de-identified D2L course-grade exports used
    while building items 1, 6 and 7. The tests don't read them; they have
    their own copies in `tests/fixtures/`.
  - `assets/` — icon artwork (`icon.ai`, `icon.eps`), not used by the app
- **Data:** `grades.db` lives in the Dropbox-synced course folder,
  `…/211 - Cell Biology/01 - Exam tool/`, so grade history follows the
  instructor to any Mac. CSV exports default to that same folder. A different
  folder can be chosen via File → Choose Data Folder….

**Rules the app follows** (decided along the way; see the items below for why):

- **Imported sheets are authoritative.** Imported grades are stored exactly as
  the sheet states them — curves and manual adjustments included — and values
  the sheet doesn't contain are never derived or filled in.
- **Raw score and percentage are reported together**, with the percentage
  leading, because an exam's point total has changed between semesters (50, 72,
  80).
- **Ranking is by percentage only**, never raw score.

## Open follow-ups

Smaller items that aren't roadmap features but are worth doing.

- ~~**Verify against the real `grades.db`.**~~ **Closed 2026-10-29.** The live
  file is only ever opened by the production copy, and it starts fresh, so there
  is no old schema to migrate and no previously-saved duplicates to merge. The
  migration code stays — it costs nothing and covers a database that has been in
  use — and is tested against a hand-built older schema in
  `tests/test_backup_restore.py`. Development uses the generated databases.
- **Validate semester codes on the Scantron path.** Historical Input rejects
  malformed codes, but a normal Scantron drop accepts whatever the file's
  `QuizClass` column says. (It does already reuse a semester saved under the
  other code format.) A warning there would close the gap.
- **Confirm the ID column headers in a real D2L course-grade export.** The
  course-export parser recognizes `OrgDefinedId`, `StudentID` and `Username`,
  but every D2L sample so far had student identity stripped. (The current
  Scantron export headers are confirmed — see "Header and student-ID checks"
  below.) Historical header examples are coming from the instructor; one real
  export run through Historical Input would also confirm it, since the preview
  shows the match count before anything is saved.
- ~~Duplicate students already in `grades.db`~~ — item 8, closed without a sweep.

### Scanned exams and the class record are separate tables — ✅ done

The app does two jobs: it scores this semester's exams so the grades can go into
D2L, and it keeps a course record to look at. They were sharing one table, which
is what produced every conflict below. Now:

- **`exam_scores`** — the current semester's **scanned** working data.
  Individual/Group rounds, key versions, missing flags; edited, re-run and
  exported through the Exam Grading section.
- **`historical_exam_scores`** — the **class record**: what a course sheet
  stated. Carries its own `possible_points`, because the sheet states its own
  MaxPoints and that is the denominator the record's percentages must use.
- **`exams` stays shared**, so one exam has one identity and the bridge query —
  this semester's exam average against past semesters' — is an ordinary join.

**The `source` flag is gone.** Which table a row is in is its provenance, so it
cannot go stale. That removes a whole class of bug: a rescanned row used to keep
claiming it came from a sheet, which silently misled both Open Saved Exam and
the duplicate-merge conflict defaults.

**What became impossible rather than merely guarded:**

| Was | Now |
|---|---|
| A course sheet overwrote the scanned grades | Different tables — the scan is untouched |
| A rescan overwrote the record, silently | Cannot reach the record at all |
| Reopening an imported exam would recompute away its curve | Record exams aren't in the table reopening reads |
| A record view that forgot its filter showed scanned data | No filter to forget |

So the warnings built for those cases are gone too; the only one left tells you
a semester has been **closed**, since scanning into a finished semester is
probably unintended. The Historical Input dialog now says the opposite of what
it used to: "This semester also has 1 scanned exam score(s); they stay in Exam
Grading and are not changed by this import."

**Duplicate students span both populations.** A merge repoints rows in both
tables, and summaries count both. A clash is only two rows for the same exam in
the *same* population — a scanned score and a record score for one exam are
different things and both survive. **A student repeating the course is not a
duplicate:** that is one student ID in two semesters, and both semesters are
theirs. Pinned by tests, along with the case that *is* a duplicate — a repeat
entered under two different IDs.

**Migration:** none needed for an empty database. For any older copy, `connect()`
moves rows that were flagged `historical_import` into the record table, where
record views can still find them, rather than leaving them stranded.

**Tests:** 309 pass. `tests/test_import_conflicts.py` now proves the separation
rather than the old warnings, and `tests/test_duplicates.py` covers both
populations and the repeating student.

### Conflicts between the two import paths — ✅ fixed

Scanning exams and importing a course sheet both write `exam_scores`, and three
ways they collided were found by running the two paths against each other with
realistic file contents. All three are prevention rather than cleanup: the
instructor's `grades.db` was empty when they were found.

**1. The same exam under two names, counted twice.** A Scantron file's
`QuizName` is `202630 Exam 1`; a course sheet's column is `Exam 1`. `exams` is
unique on (semester, name), so those were two rows for one exam and the
semester roster averaged the student's single exam twice (76.8% from "2 exams").
Fixed by normalising the name inside `get_or_create_exam` — the one place both
paths create exams — so they land on the same row and the sheet supersedes the
scan as intended. (An earlier test appeared to show the supersede already
working; it had hand-named the scanned exam `Exam 1`, which is not what a real
file contains.)

**2. A scan could overwrite the class record and then misreport itself.**
Re-scanning after a sheet import replaced the sheet's curved value with a
recomputed one, in a closed semester, silently — and left `source` still saying
`historical_import`. That flag gates Open Saved Exam and decides duplicate-merge
conflicts, so one stale value misled two other features. Fixed twice over:
`save_matched_students` now writes `source = NULL` explicitly, because the row
is what the app graded; and `store.scan_save_warnings()` lists what a save would
disturb — imported scores, a closed semester, or both — which the Results tab
shows **defaulting to No**, unlike the ordinary round-overwrite prompt.

**3. A differing point value was kept silently, making percentages wrong.**
`get_or_create_exam` filled in `possible_points` only when missing, never when
it differed. A sheet stating MaxPoints 80 against an exam recorded at 50 stored
the sheet's 35.2 and showed it as 70.4% rather than 44%. The exam still keeps
its recorded value — changing it would rescale every score already saved against
it — but the mismatch is now reported after the import that caused it, in both
the Results save message and the Historical Input summary: "Exam 1: recorded as
out of 50, this file says 80".

**Tests:** `tests/test_import_conflicts.py` (11), covering the normalisation,
provenance in both directions, the warnings and their No default, and the
mismatch being reported once and then cleared.

### Keeping development work off the live database — ✅ done

The data folder is anchored to the home directory, so **any** copy of the
project that launched the app opened the one real `grades.db`. Testing a change
in a development copy therefore touched live grades — which is what happened
when the app was opened from this folder on 20 October and ran the schema
migration against the real file.

**Two guards, checked in order** (`app/db/config.py`):

1. `EXAMGRADER_DATA_FOLDER` in the environment wins outright.
2. A file named `.dev-copy` in the project root marks a development copy, whose
   data folder becomes `<project>/dev-data`.

This folder now has `.dev-copy`, so it reads and writes
`~/Desktop/ExamGradeTool/dev-data/grades.db` and cannot reach the course folder.
`.dev-copy` and `dev-data/` are in `.gitignore`, **and must be excluded when
copying to the production folder** — a production copy with the marker would
quietly use its own empty database.

**The chosen-folder setting is isolated too.** It's per-user and shared by every
copy on the Mac, so "Choose Data Folder…" in a development copy would otherwise
move production as well. A development copy keeps its own settings file inside
its own data folder.

**Visible at a glance:** the window title appends
`— DEVELOPMENT DATA (<path>)`, and `run_app.command` prints which database it is
about to open before the window appears.

**Writing these guards immediately caught a real bug.** The long-standing
`test_set_and_get_data_folder_roundtrip` patched `CONFIG_PATH` but
`set_data_folder` resolves through `_config_path()`, so the patch was bypassed
and the test wrote a **real settings file** pointing at a pytest temp
directory. In this copy it landed harmlessly in `dev-data/`; in a production
copy it would have repointed the installed app's data folder at a deleted
temp path. `tests/conftest.py` now forces every test onto a throwaway data
folder and settings file, so no test can reach anything real again.

**Tests:** `tests/test_dev_data_isolation.py` (8) — both guards and their
precedence, the isolated settings file, the window title, and a check that this
very copy still carries its marker (a failure there means the live database is
reachable).

**Note:** a table split would not have helped here. That separates two datasets
*inside* whichever database is open; this is about *which* database is opened.

## Ideas — not being built yet

Recorded so they aren't lost. Nothing here is started, and none of it
should be built without working through its questions first.

### 13. Track and remove bad data from the database

A way to find bad data in `grades.db`, keep a record of what was removed, and
remove it under review. Supersedes item 8's narrower duplicate-student cleanup
by folding that check in, so there's one report rather than two tools.

**Suggested order: build the read-only check first, then design removal from
what it reports.** How removal should behave depends on what's actually in
there — a handful of impossible scores and two duplicate semesters is a
reviewed one-off (the item 8 pattern, with a log); hundreds of rows is a
different design. The check is also safe to run and needs no decisions.

**What "bad data" covers in this schema** — the categories the check should
report, grouped by what they break:

*Identity*
- Duplicate students: one person under two IDs. Already built as
  `app/db/duplicates.py` (decimal IDs, dropped leading zeros, typos, same-name
  pairs) — fold it in rather than duplicating it.
- Student IDs that don't fit the 7-digit rule. Blocked on import now, but rows
  saved before that are still there.
- Students with no name.

*Semesters*
- The same semester under both code formats (`2201` and `202010`), which makes
  two unrelated course offerings. Prevented for new saves, not merged for old.
- Semester codes that aren't valid at all. Still reachable today: the Scantron
  drop accepts whatever `QuizClass` says (see Open follow-ups).

*Scores*
- `final_grade` above the exam's `possible_points` — impossible.
- Negative scores. Not hypothetical: a graded sheet from the instructor held an
  Individual Score of −33.18.
- Exams with `possible_points` null or 0, and course grades with a null or 0
  denominator. **The quiet ones:** no percentage can be computed, so those
  students drop silently out of pass rates and the class-rank pool rather than
  appearing as wrong numbers. Worth reporting prominently for that reason.

*Structural*
- Orphan rows pointing at a missing student or exam. Foreign keys are enforced
  on connect so the app can't create these now, but hand-edits or rows from
  before enforcement could exist. `PRAGMA foreign_key_check` finds them.
- Exams with no scores, and semesters with nothing in them.

*Not detectable, so design for looking rather than validating*
- A score that is perfectly well-formed but attached to the wrong exam or
  semester. No rule can catch it; only the instructor can. The report should
  make records easy to read through, not just flag rule violations.

**Shape it should follow**, reusing what item 8 established: runs on a *copy* of
the database and refuses the live one; proposes, never acts on its own; writes a
log of every removal so it can be explained months later; and a backup is taken
before the fixed copy replaces the live file.

- Open questions:
  - Which categories count as worth removing versus merely worth knowing about?
    Some (an impossible score) are errors; others (a student with no name) may
    be harmless.
  - Does "remove" mean deleting rows, or marking them so they drop out of
    reports while staying in the file? Deleting grade data is irreversible, and
    the project's habit is to report rather than change.
  - Should the check become a permanent part of the app (a menu item, or run at
    launch) rather than a tool run occasionally?
- **Needs the real `grades.db`** to know which categories actually occur and in
  what numbers — the same file the Open follow-ups above are waiting on.


## Retired — not needed (9, 10, 11, 12)

**Decided 2026-10-29. These four are closed, not deferred.**

The reason is the workflow, and it applies to all of them at once: course
data is imported **historically, once the semester is over**. During the term
D2L holds the gradebook and does the arithmetic; the app's job is to score
the scanned exams and hand over a CSV to paste in. When the term ends, the
finished sheet comes in as the class record. So nothing in a D2L sheet ever
needs correcting or filling in from here — beyond editing a scanned exam's
scores, which the Results tab already does.

That also settles the question items 9 and 15 kept running into: there is
only ever **one** course grade for a student, the one D2L finished with and
the sheet states. The app never computes a second one. (This is the same
principle as "imported grades are authoritative" in `CLAUDE.md`.)

The detail worked out for each is kept below rather than deleted — the
answers were hard-won, and if the workflow ever changes they're the starting
point. **Item 10** (a ZipGrade class list) was dropped earlier for a
different reason: the Exam Scanner app replaced ZipGrade.

### 9. Calculate final course grades at the end of the semester

Work out each student's overall course grade from its parts, rather than
reading the finished number out of a D2L sheet the way item 1 does. That makes
the app the thing that produces the grade, so the grading system has to be
stated rather than inferred.

- **Letter-grade scale — answered: the WCU standard scale**, which is already
  implemented as `percent_to_letter_grade()` (A 93 / A- 90 / B+ 87 / B 83 /
  B- 80 / C+ 77 / C 73 / C- 70 / D+ 67 / D 63 / D- 60 / F below 60), confirmed
  against the cutoff table inside `Final.xls.xlsx` itself. So the
  percentage-to-letter half of this item needs no further work.
- **Still needed: how the percentage is composed.** The scale says what letter a
  percentage earns; it doesn't say how to arrive at the percentage. That part is
  still unanswered and must not be guessed from the sample sheets: the
  categories and what each is worth, whether weighting is by points or by
  percentage, whether any scores are dropped (lowest quiz, say), how extra
  credit applies, and any rounding.
- **What the sample sheets show the parts to be**, as a starting point for that
  conversation only: Exams (200), Quizzes (60), "Putting it all together"
  quizzes (16), Muddiest Point (18), Class Participation (15), Lab Grade
  (293 or 299), and two Extra Credit items (4 each). These totals differ
  between eras, so they are evidence, not a specification.
- **Per-student denominators matter.** D2L drops items a student was exempted
  from, so a category's total is not the same for everyone (see item 1). Any
  calculation has to handle an exempt item rather than scoring it zero.
- **Relationship to item 1 needs deciding.** Item 1 stores the grade the sheet
  states; this would compute one. If both exist for a semester they can
  disagree, which is worth surfacing rather than hiding — the existing rule is
  that the sheet is authoritative, so a computed grade probably shouldn't
  overwrite an imported one.


**Retired:** D2L calculates the course grade during the semester and the
finished number is imported afterwards, so the app never needs to produce
one. The WCU scale stays implemented in `percent_to_letter_grade()`, which
the Dashboard and the pass/fail line use.

### 11. Fill grades into a D2L sheet for upload

**In one line: this automates moving grades into the D2L import sheet.** It is
the last hand-done step in a workflow the app already covers — today the
instructor exports a CSV and copies a block of cells into the downloaded D2L
sheet by hand, which is the only reason `results_view.py`'s export keeps
Individual / Group / Final contiguous with a spacer after them.

Take a sheet already downloaded from D2L, write the scores into the columns it
already has, and save it back for upload. This is not about generating a file
from scratch: **the headers come from the downloaded sheet and are left
exactly as they are.** No item names, MaxPoints or `End-of-Line Indicator`
column need to be constructed or matched — they're already correct.

This is a different job from the current per-exam CSV, which is built for
copying a block of cells into the gradebook by hand (`results_view.py` keeps
Individual / Group (Used) / Final contiguous for exactly that, with a spacer
column after it). Both can sit side by side.

- **The real risk is mangling cells nobody asked to change.** Round-tripping a
  sheet through pandas quietly rewrites untouched data: `0123456` loses its
  zero, a column with one blank turns every ID into `1234567.0`, and the `#` in
  the End-of-Line Indicator can be dropped. All of those break the upload or
  attach scores to the wrong student. Read as text and write back only the
  cells being filled — for `.xlsx`, edit in place with openpyxl rather than
  rebuilding the file.
- **Match rows on `OrgDefinedId`**, comparing through
  `parsers.clean_student_id` so a 7-digit ID in the database still matches a
  6-digit one in the sheet — while the sheet's own cell keeps whatever it
  already held.
- **Blank, never zero.** A student with no saved score gets an empty cell, not
  a 0, matching how exempt exams are handled on import. A student in the sheet
  but not in the database is left alone entirely.
- **Report what happened before saving**, the way Historical Input previews an
  import: how many rows matched, which column is being filled, and which
  students in the sheet had no score to write.
- **Where the scores come from — decided: both entry points.**
  - *Results tab, after a Run* — fill the sheet with the exam just graded, from
    the run in memory (`self.matched_students`), as the existing Export Exam CSV
    already does.
  - *History tab* — pick any saved semester and exam from `exam_scores`,
    available at any time, with no Run needed.

  Two things follow that need settling when this is built, not now:
  - The Results-tab path can be filling from figures that differ from what's
    saved, if scores were edited and not yet saved to history. The existing
    export guards the related case (edited but not Recalculated); this needs the
    same care, or should simply require a save first.
  - The History path makes filling several exams into one sheet possible, since
    a D2L download holds every exam's columns at once. Whether one run does one
    exam or several is question 3 below.
  - Which column gets filled, and how is it chosen — pick from the sheet's
    existing grade columns, or match by exam name? (Note a saved exam may be
    named `202630 Exam 1` while the sheet's column is `Exam 1 - Individual`, so
    automatic matching is not a simple string comparison.)
  - Can more than one column be filled in a single pass (an exam's Individual /
    Partner / Final triple, say)?
  - Overwrite a cell that already holds a value, or leave it and report it? This
    is the one worth deciding carefully: a cell may hold a grade entered in D2L
    by hand, and an overwrite can't be undone once the sheet is uploaded.
    Leaving it and reporting matches how the rest of the app behaves, but the
    opposite view — that the app's scores are authoritative — is reasonable too.


**Retired:** grades go into D2L by pasting the exported CSV's contiguous
Individual / Group / Final block, which is a step in D2L, not here. The
export keeps that block contiguous with a spacer after it for exactly this
reason, so nothing about the current export changes.

### 12. Quiz columns: zero for non-submissions, a point for everyone else

Applies to the **quiz** columns (not discussions) in a downloaded D2L sheet,
and is mechanical: anyone who didn't do it gets **0**, and everyone who did
gets **one point added** to what they scored. Shares item 11's workflow —
open the sheet already downloaded from D2L, change only these cells, leave the
headers and everything else untouched.

**The rule, confirmed — read the number in the cell and nothing else:**

| Cell | Becomes |
|---|---|
| greater than 0 | that score **+ 1** |
| `0` | `0` |
| blank | `0` |

No need to work out who attempted and scored nothing: a 0 stays a 0 either way,
so it doesn't matter whether it was earned or never submitted.

- **Deliberate exception to "never invent data".** Everywhere else a blank score
  stays blank and is never turned into a zero (see items 1 and 6). Here writing
  0 into a blank is the point, because it's the instructor's grading decision
  rather than an inference about missing data. Worth stating plainly so a later
  pass doesn't "correct" it back.
- **`Exempt` — decided: leave the cell exactly as it is, and report how many.**
  It's neither a number nor blank, so the rule above doesn't reach it, and the
  instructor has confirmed exemptions genuinely happen. An exempt student gets
  neither the zero nor the added point; the cell is passed through untouched and
  the run says how many it found, so the count can be sanity-checked. In
  `sample_data/Final.xls.xlsx` the quiz columns hold 179 zeros, no blanks, and
  one `Exempt` — one student in that semester. Treating it as blank would have
  turned an excused student into a zero, and would also have shrunk nothing:
  D2L already reduces that student's denominator, which is the whole point of
  the exemption.
- **Flag anything over 6 rather than changing it.** A quiz score above 6 means
  something has gone wrong upstream, so the tool reports those cells — column
  and row — for the instructor to check, and leaves them alone. It does not cap,
  clamp or correct them.
- **Half points do occur, so don't assume whole numbers.** The processed sheet
  holds 3.5, 4.5 and 5.5 (51 cells across 3 columns, 41 students). A 5.5 is a
  4.5 with the point already added.
- **`sample_data/Final.xls.xlsx` is post-adjustment data**, not an example of
  raw input: the point has already been added to its quiz columns. Useful for
  knowing what output looks like, misleading as a test of input. A 6 in it is a
  5 that earned the point — which is why 6 is the legitimate ceiling and no
  cell in it exceeds its column maximum.
**Run repeatedly, on chosen columns — this is the shape of the feature.** The
instructor picks which quiz columns get the point on each run, because quizzes
are graded as the semester goes and different ones become ready at different
times. So the tool lists the quiz columns it found in the sheet and asks which
to process this time; it is not a single end-of-semester pass over everything.

**One pass per column, and no per-student bookkeeping needed.** There are no
late submissions to these entries (confirmed), so a column is processed once and
never revisited. That removes the need to remember what was written for each
student.

**A processed column is recognisable from its scores.** The quizzes are marked
out of 5, and the gradebook item allows 6 to make room for the added point — so
an unedited column tops out at 5 and an edited one contains 6s. Checked across
the processed sheet: all twelve 6-point quiz columns contain a 6. That gives a
cheap guard against running the same column twice, worth showing in the picker
("Module 02 — looks already done").

One limit on that signal, worth handling rather than trusting blindly: **it
depends on somebody scoring full marks.** If no student in a column earned 5,
the processed column would top out at 5 and look unedited. True of every column
in the sample, but it is evidence, not a guarantee — so treat it as a warning to
the instructor, never as a reason to refuse or to skip silently.
**Scope — decided: the `Category:Quizzes` columns only.** The header's category
draws the line exactly where it needs to be:

- **In scope** — `Category:Quizzes`: the eleven module quizzes, every one of
  them MaxPoints 6. These are the columns the picker offers.
- **Out of scope** — `Category:Putting it all together Quizzes`: the three
  Module 14 case-study parts (Part I at 6, Parts II and 3 at 5). The instructor
  confirmed these get no point added, so the picker shouldn't list them at all.
  This also makes Part I's scores read straightforwardly: 0 or 6 out of 6, an
  all-or-nothing raw mark rather than a 5 that gained a point.

That also settles the flag threshold: every column in scope is out of 6, so
"over 6" and "over this column's maximum" are the same test.

**Fully specified — nothing outstanding.** What remains is the build itself:
column picker, the three-line rule, the over-6 flag, the "looks already done"
warning, the exempt count, and item 11's file-integrity care (read as text,
write only the cells being changed, don't disturb IDs or the End-of-Line
Indicator).
- **Same file-integrity rules as item 11:** read as text, write only the cells
  being changed, and don't let a round trip strip a leading zero from an ID or
  drop the `#` in the End-of-Line Indicator column.


**Retired:** the quiz columns are corrected in D2L during the semester, and
the sheet that reaches the app afterwards already has the final numbers in
it. The app reads them as stated and files them under "other grades".

## Conventions

Rules that apply to anything built from here on, each with the test that
enforces it. Written up in `CLAUDE.md`:

- **Every tooltip is advertised with a greyed ⓘ beside the control**, in every
  context — buttons, combo boxes, labels, anything. A tooltip is invisible, so
  an explanation left only in one is hidden from anyone who doesn't hover. Add
  the widget through `widgets.with_hint`; the mark follows the widget's tooltip
  through Qt's ToolTipChange event, so one built later as data loads gets a mark
  too. `tests/test_hint_indicators.py` walks every window and dialog and fails
  naming any tooltip without one.
- **Every dialog's first line is its headline** — bold, two points larger, the
  rest regular weight — via `app/gui/dialogs.py`, never `QMessageBox` directly.
  `tests/test_dialogs.py` fails if a `QMessageBox` is called or built anywhere
  else.
- **Imported grades are authoritative**; bad data is reported with its row
  number, never corrected.
- **Scores are reported as raw points and a percentage**, percentage leading;
  rankings use percentages only.
- **Development never touches the live database** (`.dev-copy`,
  `EXAMGRADER_DATA_FOLDER`).
- **Anything that writes is wrapped in `@guarded`**, saves are one transaction,
  and a destructive action backs up first — a failed backup cancels it.

## Done

### Exercised on real exams — ✅ 2026-11-03

The instructor ran this version on **two real exams, with every path working**.
That is the first time the scanning side has been exercised on real data rather
than fixtures: dropping the Scantron export, Run, editing scores, Recalculate,
Save, reopening a saved exam, and the D2L CSV export, plus the dialogs and the
Dashboard behind them.

### The historical backlog, all 20 semesters — ✅ 2026-11-03

Every course sheet from Fall 2016 onwards has been imported through Historical
Input. The class record is now complete, which was the last large piece of work
on the roadmap.

It also exercises the import side on real data for the first time: the `#` on
every student ID, the textbook items left out of the component bins, the
per-student denominators from exempted items, both sheet layouts (single-score
through Fall 2021, Individual/Partner/Final from Spring 2022), and the
close-out prompt on each semester.

With a decade of course grades pooled, the class-rank tiers now have a real
pool behind them — the 0.1% tier needs 1000 course grades before it is offered
at all, and is hidden until then rather than computed from too few.

**Still unexercised on real data:** Delete Semester and Restore from Backup
against the full record. Both are tested, and both back up first.


### Maintaining the class record: delete, back up, restore — ✅ done

Built 2026-10-22 after a course sheet was imported under the wrong semester.
An import adds and overwrites but never removes, so re-importing to the right
semester left the data filed under both.

- **Delete Semester…** (Class History) removes a semester outright — its course
  grades, exams, record scores and any scanned scores — so it can be imported
  again from scratch. One transaction in foreign-key order, so a failure leaves
  the semester as it was. The confirmation names what will go, including the
  source file it came from.
  - **A repeating student is kept.** `store.semester_contents` splits students
    into those who appear nowhere else and those who also appear in another
    semester; only the first group is deleted. Deleting the rest would strip
    them out of the semester being kept.
- **Back Up Now** writes `grades_backup_YYYYmmdd_HHMMSS.db` beside `grades.db`
  itself — no folder prompt, because a backup asked about every time ends up
  scattered.
- **Restore from Backup…** replaces the contents of the open database from a
  chosen backup, showing what each side holds first. It refuses a file that
  isn't one of this app's databases, and refuses `grades.db` itself.
  - **Restore goes through the open connection** (`source.backup(conn)`, SQLite's
    backup API in reverse) rather than copying a file over `grades.db`: every
    view holds that connection, and copying would leave them reading a file
    swapped out underneath them. The schema is re-applied afterwards, so a
    backup predating a column still opens.
- **Both destructive actions back up first, and a failed backup cancels the
  action** — a backup that didn't happen is worse than none.
- **Bug found doing this:** `backup_database` named files to the second and
  would overwrite an existing one. Restore takes a safety backup immediately,
  so in the same second it could overwrite the very file being restored from,
  leaving the restore reading back what it was replacing. Backups never
  overwrite now; a collision gets `-2`.

`tests/test_delete_semester.py` (11) and `tests/test_backup_restore.py` (14).

### Reports tab, Dashboard exports, and the pass/fail line — ✅ done

- **Reports** (new tab) holds seven charts over the class record: course
  averages pooled and by semester, exam averages pooled, by semester, by exam,
  one exam across semesters, and one semester's exams. Each has a statistics
  table (n, mean, SD, SE, median, lowest, highest, pass rate) and exports three
  files at once — data, statistics and the chart image.
- **A red 70% line on every chart**, the pass/fail mark for most students:
  vertical on the histograms, where percentage is the x axis, horizontal on the
  comparison charts, where it's the y axis. The statistics table carries the
  percentage above it, because a mean can sit above the line while a third of
  the class failed.
- **Dashboard** gained the counts behind its bars as a table and two exports
  (chart image, chart data), and its x axis runs **F to A, left to right**, so
  it reads low to high like the histograms.
- **An EXAM / COURSE GRADES label above the tab bar**, and the bar divided by a
  rule, so which of the app's two jobs you're in is never in doubt.

### Interface conventions made consistent — ✅ done

Three passes over the window, 2026-10-22 to 10-29, each turned into a rule in
`CLAUDE.md` with a test behind it (see Conventions above):

- **Dialogs**: first line bold and two points larger, the rest regular weight,
  all through `app/gui/dialogs.py`. The routine "Saved" dialog became a status
  line beside the buttons — same words every time, so it didn't earn a box to
  dismiss — while a point-value mismatch still interrupts.
- **Tooltips**: every one is advertised with a greyed ⓘ beside its control, on
  buttons, the semester combo box and the rank-pool labels alike. The mark
  follows the widget's tooltip through Qt's ToolTipChange event, so one built
  later as data loads is covered too.
- **Grouping**: the Results buttons are two groups on one line — acting on the
  exam just scanned, then reaching for one already saved — divided by the same
  rule as the tab bar.
- **Rec Letter Info** lists a student as one block per semester: name, ID and
  semester on the first line, then **Course:** and **Exam:**. The ID can be
  selected with the mouse in the record, and ⌘C copies it from the match list.

### D2L course sheets: the `#` on every student ID — ✅ done

Every historical course-grade export writes the student ID as `#1234567`. The
`#` is D2L's own marker, not part of the ID, so it is detected and removed in
`parsers.clean_student_id` — the third of the three corrections there, with the
trailing `.0` and the dropped leading zero.

It composes with the other two (`#123456` → `0123456`) and **does not rescue an
ID that is wrong underneath**: `#12345` is still reported as five digits and
held out of the save. The Historical Input preview says how many IDs had one
removed rather than rewriting them silently, and the dialog gained a drop area
for the sheet. `tests/test_hash_prefixed_ids.py` (18), including an end-to-end
check that `#1234567` from one sheet and `1234567` from another resolve to one
student, not two.


### 14. Reopen a saved exam to edit and export it — ✅ built

**"Open Saved Exam…"** sits next to Run. It asks which semester (labelled in
progress or not), then which exam, and loads it into Results exactly as if its
file had just been run — then switches to that tab and says what's open. From
there the existing editing, Recalculate, Save to History and paste-ready CSV
export all work, which closes both halves of this item: a saved exam could
previously be neither edited nor re-exported.

Run is disabled while a reopened exam is loaded, since there's no dropped file
to re-run.

**Imported exams are deliberately not offered, and this is the important part.**
Reopening rebuilds the final grade from the Individual and Group scores via
`compute_grade`, so an imported exam would have its real grade silently
recalculated. Measured against the real sample: `Final.xls.xlsx`'s Exam 4
carries a flat +2 curve, and reopening it would turn 38.0 back into 36.0, 40.0
into 38.0, 43.2 into 41.2. The oldest single-score sheets are worse — they have
no component scores at all, so the students would vanish from the reconstruction
and a save would wipe their finals.

So `store.list_reopenable_exams()` offers only exams with no imported scores —
one imported row is enough to exclude an exam — and a semester with nothing
reopenable explains why: imported grades are the sheet's, including any curve,
and are corrected by importing a corrected sheet through Historical Input
(which item 15 keeps possible even for a closed semester).

This is also what makes item 15's separation real rather than named: the working
area can now be returned to, while the class record stays the sheet's.

**Tests:** `tests/test_reopen_exam.py` (10) — scores restored exactly, edit and
re-save, re-export, the tab switch and status line, both cancel paths, the
"nothing saved yet" case, and the protection: a constructed curved sheet where
the stored 36 would have become 34, plus a mixed exam excluded on one imported
row.

### 15. Separate the current semester's working data from the class history — ✅ built

**The model, from the instructor.** These are two different jobs and the app
currently blurs them:

- **Current semester — a working area.** Exams are scanned in, tracked, edited,
  and pushed to D2L. This is work in progress for the term in hand. It does
  **not** need to go into the class history.
- **Class history — the record of the whole course.** Lab grades *and* exam
  grades, imported from a separate file at the end of the semester once grades
  are complete. This is also where **export and tracking of exam grades across
  semesters** belongs.

**What the code does today.** `exam_scores` holds both populations at once, and
although the distinction is already recorded, nothing acts on it:

- Scores carry `exam_scores.source` — null when graded in the app, which is the
  working data, and `historical_import` when they came from a course sheet.
  Confirmed: the column is written on import and shown in the duplicate tool,
  but **no view filters on it.** Pass rates, percentiles, the semester roster
  and item 5's cross-semester exam export all pool the two together.
- The History tab computes an "Overall Course %" as the average of a student's
  *exam* percentages. That invents a course figure out of exam data, which is
  exactly what the model says should come from the end-of-semester file instead.
- **The end-of-semester sheet does supersede the scan, in place** — tested: a
  scanned Exam 1 of 41.6 became 35.2 with `source` flipping to
  `historical_import` after importing that semester's course sheet, still one
  row. So the handover the model wants already happens at the data level; what's
  missing is everything above it respecting the distinction.

**Decided:**

- **Closing a semester is explicit, and asked at import time.** When a course
  sheet is uploaded for a semester, the app asks whether to close that semester.
  An explicit marker rather than one derived from "has `course_grades` rows" —
  which also sidesteps a real ambiguity, since Historical Input's course-grade
  and exam-backfill checkboxes are independent, so a semester can hold course
  grades while its exam rows are still scanned ones.
- **A closed semester stays editable.** Uploading a new sheet for it must work,
  re-importing over what's there, so a correction late in the day is never
  blocked. Closing is a label on the record, not a lock.
- **In-progress semesters still appear in cross-semester reporting, labelled.**
  Not excluded — shown with an "in progress" marker so a half-graded term is
  obvious rather than silently missing. Applies to item 5's export, the
  percentile and pass-rate pools, and the History tab's semester list.
- **CSV headers do not change.** The existing export headers stay exactly as
  they are, since they're pasted into other places. Any clarification of the
  "Overall Course %" wording is on screen only — worth noting this leaves the
  on-screen label and the exported header differing, which is a deliberate
  trade for not breaking the paste workflow.

**Built (phase 1 of the plan):**

- `course_offerings.closed_at` holds the state — null means in progress — with a
  migration for existing databases. `store.set_semester_closed()`,
  `is_semester_closed()`, `in_progress_semester_codes()`,
  `closed_semester_codes()` and `describe_semester()`.
- **The close-out prompt** appears after a Historical Input import, asking
  whether to mark that semester complete, and says in so many words that a
  closed semester can still be re-imported. An already-closed semester isn't
  asked again.
- **In-progress semesters are labelled, not hidden:** the History tab's picker
  reads `202610 (Spring 2026) — in progress`; item 5's export carries
  `Spring 2026 (in progress)` inside its existing Term column, so its header row
  is untouched; and the historical-stats figures say how many semesters in the
  pool are still open.
- **Rec Letter Info explains an absence** rather than leaving a gap: "Not
  included: 202610 (Spring 2026) — in progress — class rank uses imported course
  grades, so a semester counts once its course-grade sheet is imported."
- On-screen wording for "Overall Course %" was left alone, per the instructor,
  with the explanation carried in the semester label instead — so the screen and
  the exported header still agree.
- Tests: `tests/test_semester_state.py` (10) plus updated assertions in
  `test_exam_history.py` and `test_gui_integration.py`. One of those updates is
  a guard worth keeping: the close-out prompt is a modal, and the dialog tests
  hang forever if it isn't patched.

**Built (phases 3 and 4):**

- **The tab is named "Class History"**, so the two areas say which is which:
  Results is the current semester's working area, Class History and Rec Letter
  Info are the course record.
- **Every semester states where its figures come from**, which avoids renaming
  the roster column. In progress: "No course grades imported yet, so this
  semester is in progress and not in class rank. Roster figures below are exam
  averages, not course grades." Once imported: "Course grades on record for 98
  student(s) — semester complete. Roster figures below are exam averages; course
  grades come from the imported sheet and are what class rank uses."
- **The replacement is stated twice before it happens.** The dialog's status
  line says it while the semester is being chosen ("Note: 97 exam score(s)
  graded in the app (Exam 1: 97) will be replaced by this sheet's values"), and
  importing asks outright, naming the exams and counts, explaining that the
  sheet carries any curve, and that it can't be undone from there. Declining
  writes nothing at all — not even the course grades. No prompt appears when
  there is nothing to replace.
- Tests: `tests/test_semester_state.py` now 17, covering the tab names, both
  provenance notes, the live warning, and accepting/declining the replacement.

**Note for whoever builds item 14:** the working-area line is drawn in naming
only. Results is still populated solely by a Run, so "the working area" has no
way to reopen a saved exam yet — that is item 14, and it is what would make the
separation real rather than labelled.

- Open questions:
  - **Overlaps item 9.** If the app ever computes course grades itself, that is
    a third number for the same thing. Settle how items 9, 15 and the imported
    course grade relate before building any of them.
  - Class rank stays strictly course-grade-only: a fallback to the exam average
    would silently mix two different measures in one ranking.

### Historical import: filing rules for every semester's columns — ✅ done

Decided 2026-10-21 from the headers of all 20 course-grade exports (Fall
2016 to Fall 2026, `tests/fixtures/historic_headers.xlsx`), written up in
the Claude doc "Historical Import: Column Filing". The rules are code in
`app/core/course_export.py`, so the first bulk import and every later
one-semester import file columns the same way:

- New `is_uncounted` rule: textbook / homework-platform items ("Essential
  Cell Biology …", 100 pts, no category) and placeholders ("Not Graded",
  anything under 1 point) are read but kept out of the lab / extra credit /
  other bins. The import preview lists them under "Not counted".
- Everything else already filed correctly: exams (single-score through Fall
  2021, individual/partner/final from Spring 2022), the lab by label, extra
  credit by category or label, all quizzes/participation/etc. into "other";
  subtotals, Scheme Symbol columns and the Fall 2017 Selectbox "Test" items
  ignored.
- `tests/test_historic_headers.py` pins all of this against the real headers.
- The backlog goes in through Historical Input one sheet at a time (20
  sheets, once); no batch importer was built.

### Stability fixes from the October 2026 code review — ✅ done

Four problems from a full review of the code, fixed 2026-10-21 (372 tests):

- **Failures are shown, not swallowed.** Every slot that writes — save, the
  three CSV/chart exports, backup, historical import, open saved exam — is
  wrapped by `app/gui/errors.guarded`, which turns an exception into a dialog
  (with plain words for a locked database, no permission, disk full). Before,
  only Run caught exceptions; a locked or read-only database printed a
  traceback to a terminal nobody sees and the app looked like it had saved.
  Startup also reports a database it can't open instead of exiting with a
  traceback, and a `sys.excepthook` catches anything else.
- **Saves are atomic.** `save_matched_students`, `save_course_grades` and
  `save_historical_exam_scores` each run in one `with conn:` transaction
  (`get_or_create_*` take `commit=False` inside one). A row that fails
  part-way rolls back every row before it — previously those rows sat in an
  open transaction and were committed by the next unrelated commit.
- **A blank numerator is no grade.** `CourseGradeRow.grade_basis` needs both
  halves of a pair; a D2L row with a denominator but a blank/"Exempt"
  numerator used to raise `TypeError` and abort the whole semester's import.
- **Run creates nothing; Save does.** Running a file no longer creates the
  semester and exam rows (`store.find_exam` looks up without creating;
  `MainWindow.ensure_current_exam` creates on Save). A failed Run can't leave
  Results showing one exam while Save writes under another, and a typo'd
  QuizClass no longer becomes a permanent stray semester.
- **`tools/duplicate_students.py` guards.** Refuses the production path and
  the chosen data folder, not just `default_db_path()` (which is dev-data/
  in a development copy); refuses a copy inside Dropbox without
  `--allow-synced-folder`; checks every ID in the review files against the
  database before merging anything (Excel re-saving the file strips leading
  zeros); writes `merge_log.csv` after each pair, so a failure mid-run
  leaves a log of exactly what was merged.

Second pass, same day (393 tests):

- **The live file on Dropbox.** `store.connect` sets a 15 s busy timeout
  (`BUSY_TIMEOUT_SECONDS`) so a write waits out the sync client instead of
  failing at once, and takes `create=False` to refuse a missing file. The
  app asks before creating a missing live database ("is Dropbox still
  syncing?", default No); a development copy is still created silently.
- **Backups use SQLite's online backup** (`conn.backup()`) rather than
  copying the file: a consistent snapshot even mid-transaction, including
  anything still in the rollback journal.
- **`_migrate` normalises old exam names.** Exams saved as "202610 Exam 1"
  before names were normalised are renamed to "Exam 1"; where both rows
  exist, scores move to the surviving row and a student under both keeps
  the more recently updated score (the same rule the duplicate-student
  merge uses). Without this the next import created a second exam.
- **Reports export: one semester-label lookup, not one query per row**
  (`store.describe_semesters`).
- **Rec Letter lookup.** Searches run 200 ms after typing stops
  (`SEARCH_DELAY_MS`; Return searches at once); the detail pane renders
  once per search instead of twice; `get_student_exam_averages` is a
  per-student SQL query instead of aggregating every student and filtering
  in Python; tier cutoffs are computed once per refresh and passed to
  `rank_for_percent`, which now uses a binary search.

Third pass, same day (402 tests), the low-priority items:

- Student search matches `%` and `_` literally (`LIKE … ESCAPE`).
- Indexes on `student_id` for the three score tables (schema.sql; created
  on the next connect for an existing database).
- `.xls` is refused with a plain message instead of accepted and then
  failing on the missing `xlrd` package; the file picker no longer offers it.
- A point-value mismatch is cleared at the start of each save, so an
  unread one from an earlier import is never reported as part of a later one.
- Dead code removed: `history_view._show_historical`,
  `reports_view.show_exam_across_semesters`.
- Results: an invalid score entry restores that one cell and defers the
  warning, rather than rebuilding the table inside the `itemChanged` signal
  with a modal dialog open (a known source of sporadic QTableWidget
  crashes); clearing a cell also clears its numeric sort key.
- History keeps the selected semester across refresh instead of jumping to
  the newest, so "Export Semester CSV" exports what's on screen.
- Run shows the wait cursor and disables the Run button while parsing (the
  import file picker shows the wait cursor too). Parsing still happens on
  the GUI thread; a worker thread wasn't worth the added complexity at these
  file sizes.
- `test_this_project_copy_is_marked_as_development` is skipped where the
  gitignored `.dev-copy` marker is absent (fresh clone, CI).

Not done, deliberately: per-operation database connections (the busy
timeout covers the realistic Dropbox case, and the refactor touches every
store call). A note for the test suite: windows from earlier test modules
leave matplotlib canvases with pending `draw_idle` timers, so a test that
calls `processEvents()` can hit "Internal C++ object already deleted" from
*another* module's chart. Drive timers directly in tests instead.

### 1. Historical course grade tracking (separate from exam data) — ✅ done

Overall **course** grades (lab + exams + quizzes + everything else) are
imported from the D2L course-grade export and stored separately from the
exam-only historical data.

**What the example file turned out to be.** `sample_data/Final.xls.xlsx` (98
students) is a D2L/Brightspace grade export: one row per student, one column per
graded item, with the item's maximum baked into the header text, e.g.
`Exam 1 - Individual Points Grade <Numeric MaxPoints:50 Category:Exams>`. The
open questions were answered:

- **What "adjusted" means:** adjusted numerator = calculated numerator + extra
  credit, exactly, for 97 of the 98 students; the remaining student is a manual
  instructor override (+48.25). Denominators are identical between the
  calculated and adjusted pair.
- **Why it's a pair and not a percent:** the denominator *varies per student*
  (587/585/581/543/94 in the sample) because D2L drops items a student was
  exempted from. A percent alone would lose that. `adjusted_numerator /
  adjusted_denominator * 100` reproduces the sheet's own `Grade` column exactly
  for all 98 rows.
- **Natural key:** yes — real exports lead with `OrgDefinedId`/`Username` plus
  `Last Name`/`First Name`. (The samples handed over for development had the ID
  columns stripped for privacy, which is why the importer also has a name-based
  fallback; see item 6.)
- **Shape:** one row per student per semester. The semester is *not* in the
  sheet, so it's asked for at import time.

**Implemented as:** a `course_grades` table (`app/db/schema.sql`) keyed on
`(course_offering_id, student_id)`, holding the adjusted and calculated
numerator/denominator pairs plus the derived percent, written by
`store.save_course_grades()` and read back via `store.get_course_grades()` /
`get_all_course_grades()`. Kept entirely out of `exam_scores`, as required.
Shares the Historical Input dialog with item 6 (one file feeds both).

**The oldest sheets have no adjusted pair at all.** `oldest final.xlsx` carries
only `Calculated Final Grade Numerator/Denominator` plus a `Final Grade`
percentage — no adjusted columns exist. Rather than presenting a calculated
grade as though it were adjusted, `course_grades.grade_basis` records which
pair the sheet actually provided (`"adjusted"` or `"calculated"`), and
`final_percent` is derived from that pair. Adjusted is preferred whenever both
are present. Verified against all four sample files: the percent reproduces
each sheet's own grade column exactly.

**Note:** `letter_grade` exists as a column but is deliberately left null — the
sheet is authoritative and the app does not assign grades the file doesn't
state.

### 2. Change the default data-folder location — ✅ done

`app/db/config.py`'s `DEFAULT_DATA_FOLDER` points directly at
`01 - Exam tool/` (dropped the `ExamGradeTool/data` suffix). The existing
`grades.db` was migrated there from its old location, and the now-empty
`ExamGradeTool/` remnant folder in Dropbox was removed (the `test/` folder
with the instructor's own manual export was left untouched). Verified: the
app resolves `default_db_path()` to `01 - Exam tool/grades.db` and finds the
migrated data.

### 3. Change the default save location for CSV exports — ✅ done

`results_view.py`'s Export Exam CSV and `history_view.py`'s Export Semester
CSV dialogs default to `get_data_folder()` (i.e. `01 - Exam tool/`) as the
initial directory, so both data storage and manual exports land in the same,
already-familiar course folder.

### 4. Histogram: switch to letter-grade distribution (A/B/C/D/F) — ✅ done

Cutoffs confirmed (full +/- scale, no A+): A 93, A- 90, B+ 87, B 83, B- 80,
C+ 77, C 73, C- 70, D+ 67, D 63, D- 60, F below 60. Implemented as
`percent_to_letter_grade()` (full letter incl. +/-) and `letter_grade_bucket()`
(collapses to the base A/B/C/D/F letter) in `app/core/grading.py`.
`DashboardView` buckets each student's `grade.percent` through
`letter_grade_bucket()` and renders a bar chart (`ax.bar`) over the five letters
instead of `ax.hist` on raw final grades. Covered by `tests/test_grading.py`
(cutoff table) and `tests/test_dashboard_view.py` (bar-height assertions,
missing-score exclusion, empty state).

### 5. History export: one exam across all semesters — ✅ done

"Export Exam Across Semesters…" in the History tab, beside the existing
per-semester export. It asks which exam, then where to save.

**Exam names are normalised first, which turned out to matter.** A Scantron
export names its quiz with the semester code in front — the real sample's
`QuizName` is `202610 Exam 4` — while the historical importer names exams from
the course-sheet column headers (`Exam 4`). Matching on the stored name would
never group the same exam across semesters. `app/core/exams.py` strips a leading
token *only* when it parses as a real semester code, so `2026 Exam 1` is left
alone, and sorts naturally (`Exam 10` after `Exam 2`). The picker lists one
entry per exam however it was named.

**The export is a summary, one row per student per semester:** semester code,
term label (`Fall 2016`), first and last name, student ID, final score,
possible points and final percent. Final score and percent are the only fields
every era of the data has — the older sheets record no Individual/Group
breakdown — so the export stays comparable rather than carrying columns that
are blank for older semesters. Possible points travels with the raw score
because it has changed between semesters (50, 72, 80), which is exactly why the
percentage is the figure to compare on. Rows are ordered oldest semester first,
then by name.

`store.list_exam_names()` feeds the picker and `store.get_exam_history()` pulls
every matching exam's scores across all offerings, ordered by date rather than
by text (`2201` would otherwise sort after `202610`).

**Tests:** `tests/test_exam_history.py` (21) — name normalisation and grouping,
the cross-semester query, equal percentages from different point totals, the
CSV's contents, and both cancel paths writing nothing.

### 6. Historical exam import from the course-grade Excel sheets — ✅ done

Bulk backfill of past exam grades (Exam 1–4) out of the same course-grade
sheet as item 1, into the existing `exam_scores` table.

**Header-driven detection, as required.** `app/core/course_export.py` finds the
exam columns by parsing the `<Numeric MaxPoints:N Category:X>` header suffix,
so column order doesn't matter. It deliberately does *not* match the sibling
items that sit right next to the exams in real sheets — `Student Questions -
Exam 1`, `Lecture Summary 1` — and sorts numerically, so `Exam 10` follows
`Exam 2` rather than `Exam 1`.

**Both layouts confirmed in real data and supported** (all four samples are in
`sample_data/`):

- *Individual/Partner/Final* (`Final.xls.xlsx`): three columns per exam, 50
  points each.
- *Single score* (`old final.csv`, `middle final.csv`, `oldest final.xlsx`): one
  column per exam, at **80** points in the mid-era sheets and **72** in the
  oldest. Older semesters carry no Individual/Group breakdown, and
  `possible_points` really does differ across semesters (50, 72, and 80 have
  all been seen for the same "Exam 1"). `middle final.csv` turned out to be
  identical to `old final.csv` minus its placeholder name column.

**Exempted exams are dropped, never zeroed.** Real sheets carry the literal
text `Exempt` in a score cell (and the oldest ones simply leave it blank). Such
an exam is omitted from that student's record rather than stored as a 0, which
matches the sheet reducing their denominator to compensate (555 instead of 635
in one observed row). The same applies to a student who withdrew: one row in
`Final.xls.xlsx` is 0 out of a 94-point denominator with no exam scores at
all, and imports that way.

**Key finding — the sheet's Final column is authoritative, not recomputed.**
Exams 1–3 match the app's 60/40 blend, but Exam 4 in the sample is a flat **+2
curve** on every non-zero student (94 of 97; the 3 exceptions scored 0), and
Exam 1 carries a one-off −0.9 adjustment for a single student. Running these
through `grading.compute_grade()` would silently rewrite grades the students
were actually given. So the import writes `final_grade` straight from the
sheet for *both* layouts. `individual_score`/`raw_group_score` are stored when
the sheet has them, and `effective_group_score` is left null because which
score the blend used is not recorded in the file and the importer does not
infer it. Backfilled rows are tagged `exam_scores.source = 'historical_import'`
(new column, with a migration for existing databases) so they stay
distinguishable from scores the app graded itself.

**UI:** a "Historical Input…" button next to Run opens
`app/gui/historical_import.py`, which requires a valid semester code before
anything is saved (the sheet has no semester marker — see "Semester-code guard"
below), and previews what will be imported — layout, exams and their point
values, student counts, and exactly who cannot be matched — before writing.
Course grades and exam backfill are separate checkboxes on the same dialog.

**Student identity:** keyed on `OrgDefinedId`/`StudentID` when present. When
that column is blank, `store.resolve_student_ids()` falls back to matching
last+first name against students already in the database; a name matching more
than one student is reported as unresolved rather than guessed at, and
unmatched rows are skipped, never invented.

**Closed:** a single file never spans multiple semesters (confirmed by the
instructor), so the dialog asks for the semester once per import.

**Tests:** `tests/test_course_export.py` and `tests/test_course_grades_store.py`,
including the "don't invent data" guarantees, exempt/withdrawn handling, the
calculated-only fallback, and the legacy-database migration. All four sample
sheets (2016/2019/2021/2024 eras) import cleanly end to end.

### 7. Class-rank cutoffs + student lookup ("Rec Letter Info" tab) — ✅ done

A fourth tab, **Rec Letter Info**, alongside Results / Dashboard / History,
holding two things needed when writing a recommendation letter: the class-rank
cutoffs, and a lookup for a particular student's grades.

**Class rank.** The score needed to reach the top **0.1 / 1 / 5 / 10 / 20%**,
measured against the pooled historical **course** final grades across every
semester (`course_grades.final_percent`, item 1) — via
`store.get_course_grade_pool()` and `store.class_rank_cutoffs()`.

**Percentage only, never raw score.** Cutoffs are percentages and the pool is
ranked by percentage. Not merely a display choice: a course grade's raw figure
is a numerator/denominator pair with a per-student denominator (D2L drops
exempted items), so raw numerators aren't comparable even within one semester.
Per-exam scores elsewhere still show raw and percent together; a rank cutoff
never does.

**The 0.1% tier is guarded.** Below ~1000 pooled records it resolves to a single
student — "the top scorer" wearing a more precise label. The table marks it
*provisional* and says how many grades are needed, and `rank_for_percent()`
**skips unsupported tiers entirely**, so the top student in a small pool is
labelled "Top 1%", never "Top 0.1%". This figure ends up in recommendation
letters, so it must not overstate. Against the four sample sheets (323 grades,
4 semesters) the other four tiers are well supported: 97.89 / 95.67 / 93.94 /
90.94%.

**Student lookup.** Search by last name, first name, full name, or student ID
(partial and case-insensitive). Selecting a match shows their overall course
grade per semester with the tier and rank it earns, plus every exam score with
raw points and percentage together. Auto-selects the first match.

**Ties are not split** — the nth-highest score is taken outright, so a tier can
hold slightly more students than its percentage. Stated in the tab and asserted
in tests so a future change to it is deliberate.

`RecLetterInfoView` follows the `history_view.py` pattern (constructed with
`db_conn`, exposes `refresh()`), and `main_window.py` refreshes it after a
Historical Input import so a backfill immediately deepens the pool.

**Tests:** `tests/test_class_rank.py` and `tests/test_rec_letter_view.py`,
covering tier ordering, the small-pool guard, tie behaviour, percentage-only
cutoffs, search by every field, and a regression for the detail panel going
stale when switching between searches.

### 8. Dealing with duplicate students — ✅ tool built; closed without being run

One person can sit in the database under two `student_id` values, which splits
their exam history and course grades and counts them twice in the class-rank
pool. Imports no longer create these, so what's in a database now is a fixed
set. They arose from a decimal ID (`"1234567.0"`), a dropped leading zero
(`123456` vs `0123456`), or a typo in a source file — all three now prevented.

**Built as a one-off reviewed cleanup, per the decisions below.**
`app/db/duplicates.py` holds the logic; `tools/duplicate_students.py` is the
two-step command-line tool:

```
venv/bin/python tools/duplicate_students.py report --db grades_copy.db
# review duplicates_review.csv and conflicts_review.csv
venv/bin/python tools/duplicate_students.py apply  --db grades_copy.db
```

- **Form — one-off, reviewed, never automatic.** `report` proposes pairs and
  changes nothing. Pairs whose IDs provably normalise to each other are
  pre-filled as `merge`; pairs that merely share a name are marked `review` and
  do nothing unless changed. `apply` merges only what the file says.
- **Conflicts — asked one at a time, defaulting to the imported row.** A
  conflict is only when both records hold a value for the *same* exam, or the
  same semester's course grade; everything else simply combines. Both values
  appear side by side with where each came from, and the imported row is
  pre-selected because the course sheet carries the score the student was
  actually given, curve included. Changing one `use_id` in the file picks the
  other side. Two rows of the same origin default to the newer one; course
  grades are all imported, so they use that rule too.
- **Undo — original untouched, merge log, backup before the swap.** The tool
  refuses to open the live database at all: it works on a copy, so walking away
  from a run is always possible. `merge_log.csv` records every change — which
  IDs were combined, how many rows moved, which value won each conflict — so a
  single questionable merge can be traced rather than re-derived. When the copy
  looks right, back up the live database (File → Backup Database…) and put the
  copy in its place.

**Status, 2026-11-04: closed. The tool was built and is not being run.** The
instructor decided against a duplicate sweep over the imported record, and the
student IDs came through all 20 historical imports clean, so the ID check is
closed with it.

This is a decision, not an oversight — don't reopen it or suggest running the
report. The three ID corrections on the way in (a leading `#`, a trailing `.0`,
a dropped leading zero) already rule out the mechanical ways one student became
two, and repeating students across semesters were never duplicates. The tool
stays in `tools/` with its 26 tests should a specific duplicate ever turn up and
need merging.

The logic is covered by `tests/test_duplicates.py` (26) — detection of each
kind, conflict defaults in both directions, merges across semesters, an
overridden choice, the skip path, the log's contents, and the refusal to touch
the live database.

### Raw score and percentage reporting — ✅ done

Exam scores are reported as raw points **and** percentage everywhere, with the
percentage leading, because an exam's point total has changed between semesters
and may keep changing.

- **Results tab:** a `Final %` column next to Final Grade, and the header
  states the exam's point total, e.g. "Exam 1 (out of 50 points)". Missing
  scores stay blank rather than showing 0%.
- **Exam CSV export:** `Final %` added just past the blank spacer column. The
  Individual / Group (Used) / Final block before the spacer stays in raw points
  because it's pasted straight into the D2L gradebook, which expects points.
- **History tab and its export:** the overall column is labelled
  `Overall Course %`.
- **Rec Letter Info:** exam history shows raw and percent; rank cutoffs are
  percentage only (see item 7).

### Semester-code guard — ✅ done

`app/core/semesters.py` holds the term-code rules:

| Format | Shape | Term codes | Example |
|---|---|---|---|
| Current, 6 digits | year, then term | `10` Spring, `20` Summer, `30` Fall | `202610` = Spring 2026 |
| Older, 4 digits | 3-digit year (`220` = 2020), then term | `1` Spring, `2` Summer 1, `3` Summer 2, `5` Fall | `2201` = Spring 2020 |

- **Historical Input only accepts a real code.** Free text like "Fall 2019"
  (which the dialog's own tooltip used to suggest) and unknown term digits are
  rejected. The code is read back in plain English as it's typed, and Import
  stays disabled until it's valid.
- **One semester is never split in two.** If the same semester is already saved
  under the other format (`202010` vs `2201`), the existing code is used. This
  check is in `store.get_or_create_course_offering`, so the normal Scantron drop
  gets it too.
- **Summer is not merged across formats.** The older format had two summer
  sessions and the current one has a single Summer term, with no stated match
  between them. Instead, the dialog warns if the other format's summer is
  already saved for the same year.
- **Semesters are sorted by date, not as text.** As text, `2205` sorts above
  `202610`, putting Fall 2020 above Spring 2026. The History dropdown, the
  Historical Input list, and Rec Letter Info all sort by date and show labels
  like `2201 (Spring 2020)`.

Tested in `tests/test_semesters.py` and `tests/test_historical_import_dialog.py`.

### Header and student-ID checks — ✅ done

**Scantron export headers confirmed.** The instructor's `headers.xlsx` ("RAw"
sheet) has the current Scantron export header row: 112 columns, all nine the
parser requires (`QuizName`, `QuizClass`, `FirstName`, `LastName`, `StudentID`,
`CustomID`, `Earned Points`, `Possible Points`, `Key Version`), spelled exactly,
with no stray spaces or duplicates. The rest — `PercentCorrect`, `QuizCreated`,
`DataExported`, and per-question `Stu`/`PriKey`/`Points`/`Mark` for 25
questions — are ignored. Recorded as a test in `tests/test_parsers.py`.

**Student-ID fix.** Both parsers now put IDs through
`parsers.normalize_student_id`, which fixes two ways spreadsheets mangle them.
Either one stops a student matching their own records from another file:

- **A decimal point.** If even one row in a file had a blank ID, pandas read
  the whole column as decimals, so `1234567` came through as `"1234567.0"`.
- **A dropped leading zero on a 6-digit ID.** Student IDs are always 7 digits
  (confirmed by the instructor), so `123456` becomes `0123456`. A CSV loses the
  zero while an Excel cell holding the ID as text keeps it, so the same student
  arrived under two different IDs depending on the file.

**Everything else is reported, never corrected.** An ID of any other length, or
one that isn't all digits, is left exactly as the file states it and listed with
its row number — the row as the file opens in Excel, header being row 1 — so it
can be fixed at the source. Guessing at what the right ID was is precisely how
one student becomes two. Those rows are also **held out of the save**, the same
way duplicate exam forms already are, so a bad ID can't create a second student
record. The Results tab lists them under the duplicate-forms notice, and the
Historical Input preview lists them before anything is written:

```
Student ID problems (2) — student IDs are 7 digits. Held out of this save;
correct these rows in the file and re-upload:
Row 4: ID 12345 — 5 digits, expected 7.
Row 5: ID X9999 — not a number.
```

Both affected Scantron exams (CSV and Excel) and D2L course exports alike.
Tests are in `tests/test_parsers.py`, `tests/test_course_export.py` and
`tests/test_course_grades_store.py`, including an end-to-end check that a
6-digit ID from one file and a 7-digit ID from another resolve to the same
student, and that flagged rows never reach the database. The Scantron test
fixtures now use realistic 7-digit IDs, while the course-export fixtures keep
6-digit ones so the padding stays exercised. Records already saved in the older
forms are covered by item 8.

### Installer removed; runs from `run_app.command` — ✅ done

The py2app/DMG build is gone: `packaging/` (build scripts), `build/`, and
`dist/` (the 1.9 GB app bundle and DMG) were moved to the Trash, and the
build-only packages (`py2app`, `setuptools<81`, `jaraco.text`) were removed from
`requirements.txt` and `venv/`. The DMG had last been built in July 2026 and was
missing every feature since.

`run_app.command` is now the only way to run the app, and it sets itself up:

- **Rebuilds its environment when needed.** A `venv/` is tied to the Mac and
  Python it was built with — this one points at a conda install in
  `/opt/miniconda3` — so a copied folder won't run as-is. The launcher checks
  that `venv/` exists, that its interpreter runs, and that PySide6, pandas,
  openpyxl and matplotlib are installed. If any check fails, it rebuilds
  `venv/` from `requirements.txt`.
- **Finds a suitable Python.** It uses the first Python 3.9–3.13 it finds,
  including the one built into macOS. The code was checked to run on 3.9.
- **Keeps error messages readable.** If setup or the app fails, the Terminal
  window stays open so the message can be read.

`--check` reports whether setup is needed without installing anything.
