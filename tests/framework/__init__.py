"""
tests/framework - Lightweight, extensible testing and evaluation framework for coding agents.
"""

from tests.framework.assertions import *
from tests.framework.runner import *
from tests.framework.spec import *

__all__ = [
    *assertions.__all__,
    *runner.__all__,
    *spec.__all__,
]

