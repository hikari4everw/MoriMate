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
