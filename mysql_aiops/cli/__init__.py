"""CLI package for mysql-aiops.

Re-exports ``app`` so the pyproject entry point
``mysql-aiops = "mysql_aiops.cli:app"`` works unchanged.
"""

from mysql_aiops.cli._root import app

__all__ = ["app"]
