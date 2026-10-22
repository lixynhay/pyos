"""Общие фикстуры для тестов pyOS."""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyos.kernel import Kernel  # noqa: E402
from pyos.shell import Shell  # noqa: E402


@pytest.fixture
def kernel():
    return Kernel()


@pytest.fixture
def root(kernel):
    return kernel.users.get_by_name("root")


@pytest.fixture
def user(kernel):
    return kernel.users.get_by_name("user")


@pytest.fixture
def shell(kernel, root):
    return Shell(kernel, root)
