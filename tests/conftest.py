import pytest

from epilens.atlas import Atlas


@pytest.fixture(scope="session")
def atlas():
    return Atlas.load()
