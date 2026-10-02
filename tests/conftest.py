from __future__ import annotations

import pytest

from tests import fixture_builder as fb


class FixtureFactory:
    def __init__(self, root):
        self.root = root
        self._built: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._built:
            path = str(self.root / f"{name}.pdf")
            getattr(fb, f"build_{name}")(path)
            self._built[name] = path
        return self._built[name]


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> FixtureFactory:
    return FixtureFactory(tmp_path_factory.mktemp("fixtures"))
