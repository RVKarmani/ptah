"""Deterministic furniture layout planner (Holodeck-style constraint solver)."""
from .constraints import hard_violations
from .solver import Budget, SolveResult, plan_layout, solve

__all__ = ["Budget", "SolveResult", "plan_layout", "solve", "hard_violations"]
