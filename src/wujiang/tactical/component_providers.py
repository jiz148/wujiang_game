"""Object-owned proofs for conditional, read-only component providers.

The declaration certifies only the empty branch of this exact function. A
nonempty child, changed method or unusual field keeps ordinary discovery.
"""
from types import FunctionType


class EmptyComponentBranch:
    __slots__ = ('field', 'function', 'code', 'defaults', 'kwdefaults', 'closure')

    def __init__(self, field, function):
        self.field, self.function, self.code = field, function, function.__code__
        self.defaults, self.kwdefaults = function.__defaults__, function.__kwdefaults__
        self.closure = function.__closure__

    def matches(self, function):
        return (function is self.function and function.__code__ is self.code
                and function.__defaults__ is self.defaults
                and function.__kwdefaults__ is self.kwdefaults
                and function.__closure__ is self.closure)


def empty_components_when_none(field):
    """Reviewed provider returns () without other reads when field is None."""
    if type(field) is not str or not field.isidentifier():
        raise TypeError('A plain instance field is required')
    def declare(function):
        if type(function) is not FunctionType or function.__defaults__ or function.__kwdefaults__ or function.__closure__:
            raise TypeError('A plain provider method is required')
        function._empty_component_branch = EmptyComponentBranch(field, function)
        return function
    return declare


def readonly_component_fields(*fields, optional=False):
    """The reviewed provider only returns these stored child objects.

    This proves query purity, not eligibility for cached component discovery.
    """
    if type(optional) is not bool or not fields or any(type(field) is not str or not field.isidentifier() for field in fields):
        raise TypeError('Plain child fields are required')
    def declare(function):
        if type(function) is not FunctionType or function.__defaults__ or function.__kwdefaults__ or function.__closure__:
            raise TypeError('A plain provider method is required')
        function._readonly_component_fields = tuple(EmptyComponentBranch(field, function) for field in fields)
        function._readonly_optional_children = optional
        return function
    return declare


