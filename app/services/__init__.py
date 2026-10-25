"""Domain logic with no routes: the code blueprints share (#489).

A blueprint imports from here, never from another blueprint, and nothing here
imports a blueprint. `tests/test_module_boundaries.py` holds both rules, and its
allowlist has been empty since #489 finished. Keep it that way.
"""
