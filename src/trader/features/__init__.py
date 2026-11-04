"""Point-in-time features (ARCHITECTURE §3): pure functions of a Panel, no I/O, no wall clock.

Every feature is a T x N float array whose row t uses only data knowable at the close of t
(rows 0..t of the panel). tests/test_features.py enforces this with a truncation check.
"""
