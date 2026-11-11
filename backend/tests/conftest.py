from pathlib import Path
from tempfile import TemporaryDirectory

from tests.offline_environment import configure_offline_environment

_OFFLINE_DATA = TemporaryDirectory(prefix="novel-pytest-")
configure_offline_environment(Path(_OFFLINE_DATA.name))


def pytest_unconfigure() -> None:
    _OFFLINE_DATA.cleanup()
