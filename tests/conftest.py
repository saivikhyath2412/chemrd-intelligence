import pytest


@pytest.fixture(autouse=True)
def isolated_public_structure_cache(monkeypatch, tmp_path):
    # Mock identities must never contaminate the user's persistent cache.
    monkeypatch.setenv('CHEMRD_STRUCTURE_CACHE', str(tmp_path / 'structures'))
