"""Qualification predicates must remain active under Python -O."""


def require(condition, message):
    if not condition:
        raise AssertionError(message)
