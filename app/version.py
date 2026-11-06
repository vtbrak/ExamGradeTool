"""The app's version, in one place.

`STAGE` is what the version is *for*: "beta" says the app is in real use but
still being changed in response to that use, which is exactly where it is —
two exams and all twenty historical sheets have been through it, and the
interface has moved under the instructor's feet several times since.

The version appears in the window title, so a screenshot or a support question
says which copy it came from, and `run_app.command` prints it at startup.
Bump VERSION here and nowhere else; the git tag should match (`v1.1-beta`).
"""

VERSION = "1.1"
STAGE = "beta"

# "1.1 beta", or just "1.2" once a version is no longer a beta.
LABEL = f"{VERSION} {STAGE}".strip() if STAGE else VERSION

APP_NAME = "Cell Biology Exam Grader"
TITLE = f"{APP_NAME} {LABEL}"
