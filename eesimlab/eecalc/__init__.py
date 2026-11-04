"""Electrical engineering calculator: calculation logic (no GUI).

Importing this package registers every calculator in ``core.REGISTRY``.
"""
from eesimlab.eecalc import basic, filters, rlc, tools, transient, conversions, practical  # noqa: F401  (registration side effects)
from eesimlab.eecalc.core import CATEGORIES, REGISTRY, CalcError, get, run, search  # noqa: F401
