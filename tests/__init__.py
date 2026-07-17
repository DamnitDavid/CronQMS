"""Test suite package.

All test modules share a single SQLite database (``test_cronqms.db``). Each
module also sets ``DATABASE_URL`` itself before importing the app, because
``unittest discover`` imports test modules as top-level names and does not run
this package initializer. This line is a fallback for package-style imports
(e.g. ``python -m unittest tests.test_x``); ``setdefault`` keeps the first
writer's value, so the two mechanisms never conflict.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_cronqms.db")
