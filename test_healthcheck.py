"""Compatibility shim for ``python -m unittest test_healthcheck -v``.

The canonical acceptance tests live in tests/test_healthcheck.py. This module
re-exports them so the exact command requested from the repository root also
works without setting PYTHONPATH or changing the working directory.
"""

from tests.test_healthcheck import *  # noqa: F401,F403
