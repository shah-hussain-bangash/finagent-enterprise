"""Specialist capabilities behind the model gateway (prompt §8-§11; AER-001..027).

Capabilities never touch the database, shell, filesystem or network. Deterministic orchestration code
prepares their input from an evidence snapshot, the model gateway is the only path to a model, and
deterministic validators decide what output is accepted.
"""
