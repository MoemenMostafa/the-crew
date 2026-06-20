from crew.state import SessionStore


def test_set_get_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "state" / "sessions.json")
    assert store.get("adam") is None
    store.set("adam", "sess-1")
    assert store.get("adam") == "sess-1"


def test_persists_across_instances(tmp_path):
    path = tmp_path / "state" / "sessions.json"
    SessionStore(path).set("adam", "sess-9")
    # A fresh instance (simulating a process restart) sees the saved id.
    assert SessionStore(path).get("adam") == "sess-9"


def test_independent_personas(tmp_path):
    path = tmp_path / "sessions.json"
    s = SessionStore(path)
    s.set("adam", "a1")
    s.set("eva", "e1")
    assert s.get("adam") == "a1"
    assert s.get("eva") == "e1"


def test_corrupt_file_is_tolerated(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("{not json")
    store = SessionStore(path)
    assert store.get("adam") is None  # no crash
    store.set("adam", "a1")
    assert store.get("adam") == "a1"
