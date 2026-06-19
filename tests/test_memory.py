from crew.memory import Memory


def test_read_includes_index_and_topic_files(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text("# index\n- [tts](tts.md) — gotcha")
    (mem_dir / "tts.md").write_text("Kokoro needs espeak-ng.")

    text = Memory(mem_dir).read()
    assert "index" in text
    assert "Kokoro needs espeak-ng." in text


def test_append_note_creates_topic_and_indexes_once(tmp_path):
    mem_dir = tmp_path / "memory"
    m = Memory(mem_dir)

    m.append_note("tts", "Kokoro needs espeak-ng.")
    m.append_note("tts", "Also: 700MB voices download on first run.")

    text = m.read()
    assert "Kokoro needs espeak-ng." in text
    assert "700MB voices" in text
    # The topic is indexed exactly once even after two appends.
    index = (mem_dir / "MEMORY.md").read_text()
    assert index.count("(tts.md)") == 1
