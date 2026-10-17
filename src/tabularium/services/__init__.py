"""Domain operations. Each one validates invariants and writes its audit entry.

Callers own the transaction: services add/flush, the caller commits.
"""


class DomainError(Exception):
    """A rule of the domain was violated; the message is meant for the user."""
