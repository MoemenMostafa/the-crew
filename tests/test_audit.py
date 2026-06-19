import json

from crew.audit import AuditLog


def test_records_one_json_line_per_event(tmp_path):
    path = tmp_path / "audit.jsonl"
    clock = iter([100.0, 200.0])
    log = AuditLog(path, clock=lambda: next(clock))

    log.record(persona="adam", tool="Bash", summary="git status", decision="allow")
    log.record(persona="adam", tool="Edit", summary="app/main.py", channel="#adam-dev")

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["persona"] == "adam"
    assert first["tool"] == "Bash"
    assert first["summary"] == "git status"
    assert first["decision"] == "allow"
    assert first["ts"] == 100.0

    second = json.loads(lines[1])
    assert second["channel"] == "#adam-dev"
    assert second["ts"] == 200.0
