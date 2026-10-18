from open_llm_vtuber.companion.memory import MemoryStore


def test_persists_verified_memory_and_correction(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = MemoryStore(path)
    source = store.add_source("user", "我喜欢红茶。")
    item = store.remember("preference", "喜欢红茶", [source], "我喜欢红茶")
    store.correct(item, "现在更喜欢绿茶")
    store.close()
    reopened = MemoryStore(path)
    assert reopened.memories()[0]["text"] == "现在更喜欢绿茶"
    assert reopened.memories()[0]["origin"] == "user_edit"
    assert reopened.sources() == []  # obsolete source cannot override the edit


def test_delete_invalidates_dependent_summary_and_blocks_reextraction(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    source = store.add_source("user", "我住在月亮上。")
    item = store.remember("fact", "用户说住在月亮上", [source], "我住在月亮上")
    store.save_summary("daily", "用户住在月亮上", [source])
    store.forget(item)
    assert store.memories() == []
    assert store.summary("daily") is None
    assert store.sources() == []
    assert store.remember("fact", "住月亮", [source], "我住在月亮上") is None


def test_rejects_unobserved_evidence_and_assistant_guesses(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    a = store.add_source("assistant", "你一定喜欢咖啡")
    b = store.add_source("user", "今天好冷")
    assert store.remember("preference", "喜欢咖啡", [a], "喜欢咖啡") is None
    assert store.remember("preference", "喜欢咖啡", [b], "喜欢咖啡") is None


def test_story_context_isolated_and_survives_restart(tmp_path):
    path = tmp_path / "m.db"
    store = MemoryStore(path)
    s = store.add_source("observation", "小明走进教室", "game-a")
    store.save_summary("game-a", "小明已走进教室", [s])
    store.add_source("observation", "另一个游戏", "game-b")
    assert store.sources(scene="game-a")[0]["text"] == "小明走进教室"
    assert MemoryStore(path).summary("game-a")["text"] == "小明已走进教室"


def test_stale_summary_cannot_be_saved_after_forgetting(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    s = store.add_source("user", "我喜欢下雨")
    m = store.remember("preference", "喜欢下雨", [s], "喜欢下雨")
    store.forget(m)
    assert store.save_summary("daily", "喜欢下雨", [s]) is None


def test_deletion_invalidates_summary_of_assistant_paraphrase(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    source = store.add_source("user", "我喜欢咖啡")
    item = store.remember("preference", "喜欢咖啡", [source], "喜欢咖啡")
    paraphrase = store.add_source("assistant", "你是咖啡爱好者")
    store.save_summary("daily", "用户爱喝咖啡", [paraphrase])
    store.forget(item)
    assert store.summary("daily") is None
    assert store.sources() == []


def test_legacy_corrected_memory_with_lost_scope_is_quarantined(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, kind TEXT, text TEXT, source_ids TEXT, evidence TEXT, origin TEXT)"
        )
        db.execute(
            "INSERT INTO memories VALUES(1,'experience','旧游戏纠正记忆','[]','用户直接修改','user_edit')"
        )
    store = MemoryStore(path)
    assert store.memories()[0]["scene"] == "unknown"


def test_summary_exposes_recorded_range_from_its_actual_sources(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    first = store.add_source("user", "昨天去看海了")
    last = store.add_source("user", "今天回来了")
    with store.db:
        store.db.execute(
            "UPDATE sources SET created=? WHERE id=?",
            ("2026-10-17T02:00:00+00:00", first),
        )
        store.db.execute(
            "UPDATE sources SET created=? WHERE id=?",
            ("2026-10-18T01:00:00+00:00", last),
        )
    store.save_summary("daily", "用户提到看海和返程。", [first, last])
    summary = store.summary("daily")
    assert summary["recorded_from"] == "2026-10-17T02:00:00+00:00"
    assert summary["recorded_to"] == "2026-10-18T01:00:00+00:00"


def test_observation_history_does_not_expand_back_to_an_old_user(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    store.add_source("user", "开始看游戏")
    for n in range(300):
        store.add_source("observation", f"对白{n}")
        store.add_source("assistant", f"评论{n}", speech=f"コメント{n}")
    store.add_source("user", "刚才怎么了")
    rows = store.conversation_sources("daily", limit=20)
    assert len(rows) == 21
    assert rows[0]["kind"] == "observation"
    assert rows[-1]["text"] == "刚才怎么了"
