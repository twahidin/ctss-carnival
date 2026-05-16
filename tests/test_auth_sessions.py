import pytest

from auth import make_session, read_session


def test_make_and_read_session_roundtrip() -> None:
    cookie = make_session(role="booth", booth_id=42)
    data = read_session(cookie)
    assert data is not None
    assert data["role"] == "booth"
    assert data["booth_id"] == 42


def test_read_session_rejects_tampered_cookie() -> None:
    cookie = make_session(role="admin")
    tampered = cookie[:-2] + "xx"
    assert read_session(tampered) is None


def test_read_session_rejects_expired(monkeypatch) -> None:
    import time
    real_now = time.time()
    cookie = make_session(role="admin")
    monkeypatch.setattr("time.time", lambda: real_now + 13 * 3600)
    assert read_session(cookie) is None
