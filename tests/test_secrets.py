import pytest

from wtisen_runner.secrets import resolve_secret


def test_secret_from_env(monkeypatch):
    monkeypatch.setenv("TEST_SECRET", "value1")
    assert resolve_secret("TEST_SECRET") == "value1"


def test_secret_missing_raises(monkeypatch):
    monkeypatch.delenv("TEST_SECRET", raising=False)
    with pytest.raises(ValueError):
        resolve_secret("TEST_SECRET")
