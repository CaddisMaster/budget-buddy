"""Domain logic with no routes: the code blueprints share (#489).

A blueprint imports from here, never from another blueprint, and nothing here
imports a blueprint. `tests/test_module_boundaries.py` holds both rules, with an
allowlist of the edges that predate them; it may only shrink.
"""
