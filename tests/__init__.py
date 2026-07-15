"""Test suite package.

All test modules share a single SQLite database and one module-level engine
(``app.database.engine``). Establishing ``DATABASE_URL`` here — before any test
module imports the application — guarantees every module agrees on the same
database file, so running them together under ``unittest discover`` behaves the
same as running them individually.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_proins.db")
