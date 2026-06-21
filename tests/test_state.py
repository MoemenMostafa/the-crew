from crew.state import SessionStore


def test_set_get_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "state" / "sessions.json")
    assert store.get("adam", "#t1") is None
    store.set("adam", "#t1", "sess-1")
    assert store.get("adam", "#t1") == "sess-1"


def test_persists_across_instances(tmp_path):
    path = tmp_path / "state" / "sessions.json"
    SessionStore(path).set("adam", "#t1", "sess-9")
    # A fresh instance (simulating a process restart) sees the saved id.
    assert SessionStore(path).get("adam", "#t1") == "sess-9"


def test_independent_conversations_and_personas(tmp_path):
    path = tmp_path / "sessions.json"
    s = SessionStore(path)
    s.set("adam", "thread-a", "a1")
    s.set("adam", "thread-b", "a2")
    s.set("eva", "thread-a", "e1")
    assert s.get("adam", "thread-a") == "a1"
    assert s.get("adam", "thread-b") == "a2"
    assert s.get("eva", "thread-a") == "e1"
    # Unknown conversation for a known persona is absent, not a crash.
    assert s.get("adam", "thread-z") is None


def test_corrupt_file_is_tolerated(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("{not json")
    store = SessionStore(path)
    assert store.get("adam", "#t1") is None  # no crash
    store.set("adam", "#t1", "a1")
    assert store.get("adam", "#t1") == "a1"


def test_legacy_flat_format_is_ignored(tmp_path):
    # Pre-upgrade files stored a single id string per persona; those should be
    # treated as absent (conversation starts fresh once) rather than crashing.
    path = tmp_path / "sessions.json"
    path.write_text('{"adam": "old-flat-id"}')
    store = SessionStore(path)
    assert store.get("adam", "#t1") is None
    store.set("adam", "#t1", "new-id")
    assert store.get("adam", "#t1") == "new-id"
