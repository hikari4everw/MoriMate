import sqlite3
from open_llm_vtuber.companion.memory import MemoryStore


def legacy(path):
    db = sqlite3.connect(path)
    db.executescript('''
      CREATE TABLE sources(id INTEGER PRIMARY KEY,kind TEXT,text TEXT,scene TEXT,created TEXT,redacted INTEGER DEFAULT 0,processed INTEGER DEFAULT 0,speech TEXT);
      CREATE TABLE memories(id INTEGER PRIMARY KEY,kind TEXT,text TEXT,source_ids TEXT,evidence TEXT,origin TEXT,scene TEXT);
      CREATE TABLE summaries(scene TEXT PRIMARY KEY,text TEXT,source_ids TEXT);
      CREATE TABLE profile(key TEXT PRIMARY KEY,value TEXT);
      INSERT INTO sources VALUES(1,'user','旧偏好咖啡','daily','2026-01-01T00:00:00+00:00',0,0,NULL);
      INSERT INTO memories VALUES(1,'preference','旧偏好咖啡','[1]','咖啡','user',NULL);
      INSERT INTO summaries VALUES('daily','旧摘要咖啡','[1]');
      INSERT INTO profile VALUES('persona','自定义人格');
    ''')
    db.close()


def test_legacy_content_is_visible_only_in_archive_and_migration_is_idempotent(tmp_path):
    path = tmp_path / 'memory.sqlite3'
    legacy(path)
    store = MemoryStore(path)
    assert store.sources() == []
    assert store.memories() == []
    assert store.summary('daily') is None
    assert store.persona('default') == '自定义人格'
    archived = store.archive()
    assert archived['sources'][0]['text'] == '旧偏好咖啡'
    assert archived['memories'][0]['text'] == '旧偏好咖啡'
    assert archived['summaries'][0]['text'] == '旧摘要咖啡'
    new_id = store.add_source('user', '新约定')
    store.close()
    reopened = MemoryStore(path)
    assert [row['id'] for row in reopened.sources()] == [new_id]
    assert len(reopened.archive()['sources']) == 1
    assert not list(tmp_path.glob('*.migration-backup*'))


def test_cannot_build_memory_or_summary_using_archived_sources(tmp_path):
    path = tmp_path / 'memory.sqlite3'
    legacy(path)
    store = MemoryStore(path)
    assert store.remember('preference', '咖啡', [1], '咖啡') is None
    assert store.save_summary('daily', '旧信息', [1]) is None
    assert store.conversation_sources('daily') == []


def test_turn_ids_persist_and_group_assistant_sentences(tmp_path):
    path = tmp_path / 'm.sqlite3'
    store = MemoryStore(path)
    store.add_source('user', '第一回合')
    store.add_source('assistant', '回答一')
    store.add_source('assistant', '回答二')
    first = store.sources()[0]['conversation_turn']
    assert {s['conversation_turn'] for s in store.sources()} == {first}
    store.close()
    store = MemoryStore(path)
    store.add_source('user', '第二回合')
    assert store.sources()[-1]['conversation_turn'] != first


def test_provenance_schema_failure_rolls_back_before_retry(tmp_path):
    import pytest
    from open_llm_vtuber.companion.memory_migrations import migrate_v2, migrate_provenance
    path = tmp_path / 'migration.db'
    legacy(path)
    db = sqlite3.connect(path)
    migrate_v2(db, path)
    def authorize(action, arg1, arg2, database, trigger):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ALTER_TABLE and arg2 == 'memories' else sqlite3.SQLITE_OK
    db.set_authorizer(authorize)
    with pytest.raises(sqlite3.DatabaseError):
        migrate_provenance(db)
    db.set_authorizer(None)
    assert 'prompt_excluded' not in {r[1] for r in db.execute('PRAGMA table_info(sources)')}
    migrate_provenance(db)
    assert 'state' in {r[1] for r in db.execute('PRAGMA table_info(memories)')}
