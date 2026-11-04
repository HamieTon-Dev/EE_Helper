"""Electrical engineering calculator: calculation logic (no GUI).

Importing this package registers every calculator in ``core.REGISTRY``.
"""
from mscopilot.eecalc import basic, filters, rlc, tools, transient  # noqa: F401  (registration side effects)
from mscopilot.eecalc.core import CATEGORIES, REGISTRY, CalcError, get, run, search  # noqa: F401
