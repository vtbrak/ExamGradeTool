# Conventions for this project

Short, enforced rules. Each one has a test that fails if it's broken, named
beside it — so these aren't a style guide to remember, they're checks.

## Every tooltip is advertised with an information mark

A tooltip is invisible: anyone who doesn't happen to hover never learns the
explanation is there. So **wherever a widget gets `setToolTip`, a greyed ⓘ goes
beside it.** This applies in every context, not just buttons — combo boxes,
labels, check boxes, table headers, dialogs, anything.

Add the widget to its layout through `with_hint` (`app/gui/widgets.py`):

```python
from app.gui.widgets import with_hint

row.addWidget(with_hint(self.export_button))
form.addRow("Semester code:", with_hint(self.semester_combo))
```

The mark follows the widget's tooltip through Qt's `ToolTipChange` event, so a
tooltip built later — several are rebuilt as data loads — gets its mark too,
and the mark hides itself while the tooltip is empty.

*Enforced by `tests/test_hint_indicators.py`, which walks every window and
dialog and fails naming any tooltip without a mark.*

## Every dialog's first line is its headline

Pop-ups are written as plain text with the headline on the first line, and go
through `app/gui/dialogs.py` — never `QMessageBox` directly. The helper renders
the first line bold and two points larger, the rest at regular weight.

```python
from app.gui import dialogs

dialogs.information(self, "Backup complete", f"The database is backed up to:\n{dest}")
```

*Enforced by `tests/test_dialogs.py`, which fails if any `QMessageBox` is
called or constructed outside the helper.*

## Imported grades are authoritative

Never derive, recalculate or fill in a value the source sheet doesn't state. A
sheet can embed a curve or a manual adjustment that no formula here knows
about. Anything that looks wrong is **reported with its row number**, never
corrected — guessing at what a value should have been is how one student
becomes two.

The only exceptions are three ways a spreadsheet or the LMS mangles a student
ID that was right to begin with, all in `parsers.clean_student_id`: a leading
`#`, a trailing `.0`, and a dropped leading zero on a 6-digit ID.

## Scores are reported as raw points and a percentage

Both, with the percentage leading. Exam point totals have changed between
semesters (80, 72, 50) and may change again, so only the percentage is
comparable. Rankings and cutoffs use percentages only.

## Development never touches the live database

A `.dev-copy` marker in the project root, or `EXAMGRADER_DATA_FOLDER`, sends
the app to `dev-data/` instead of the Dropbox course folder, and the window
title and launcher say so. Tools that write (`tools/generate_dev_data.py`,
`tools/duplicate_students.py`) refuse to run against the live path.

*Enforced by `tests/test_dev_data_isolation.py` and `tests/conftest.py`, which
forces every test onto a throwaway data folder.*

## Anything that writes is wrapped

Database writes, CSV and chart exports, backups and imports go through
`@guarded` (`app/gui/errors.py`). A Qt slot that raises doesn't crash the app —
it prints a traceback to a terminal nobody watches, and a failed save looks
exactly like a successful one.

Saves run in one transaction, and a destructive action backs up first; if the
backup fails, the action is cancelled rather than proceeding without it.

## Tests must never open a real modal

A `QMessageBox` left unpatched blocks forever and hangs the whole suite. Patch
the statics the code calls (`question`, `information`, `warning`, `critical`).
Run with `QT_QPA_PLATFORM=offscreen`, in the `examgradetool` conda
environment that `install examgradetool.command` creates.

```bash
QT_QPA_PLATFORM=offscreen conda run --no-capture-output -n examgradetool python -m pytest -q
```
