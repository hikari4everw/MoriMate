"""A transactional boundary: old data stays viewable, never becomes new context."""
import sqlite3


def migrate_v2(db, path):
    marker = db.execute("SELECT value FROM profile WHERE key='_memory_epoch'").fetchone()
    if marker:
        return {"epoch": int(marker[0]), "migrated": False}
    backup_path = path.with_suffix(path.suffix + '.migration-backup')
    has_data = any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in ('sources', 'memories', 'summaries', 'profile'))
    if has_data:
        backup = sqlite3.connect(backup_path)
        try:
            db.backup(backup)
        finally:
            backup.close()
        backup_path.chmod(0o600)
    try:
        db.execute('BEGIN IMMEDIATE')
        for table in ('sources', 'memories'):
            db.execute(f'ALTER TABLE {table} ADD COLUMN epoch INTEGER NOT NULL DEFAULT 0')
        db.execute('ALTER TABLE sources ADD COLUMN conversation_turn INTEGER')
        db.execute('CREATE TABLE conversation_turns (id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL, epoch INTEGER NOT NULL)')
        db.execute('ALTER TABLE summaries RENAME TO summaries_old')
        db.execute('CREATE TABLE summaries (scene TEXT NOT NULL,text TEXT NOT NULL,source_ids TEXT NOT NULL,epoch INTEGER NOT NULL,PRIMARY KEY(scene,epoch))')
        db.execute('INSERT INTO summaries SELECT scene,text,source_ids,0 FROM summaries_old')
        db.execute('DROP TABLE summaries_old')
        db.execute("INSERT INTO profile VALUES('_memory_epoch','1')")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    else:
        backup_path.unlink(missing_ok=True)
    return {"epoch": 1, "migrated": True}


def migrate_provenance(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(sources)')}
    if 'prompt_excluded' in columns:
        return
    with db:
        db.execute('ALTER TABLE sources ADD COLUMN prompt_excluded INTEGER NOT NULL DEFAULT 0')
        for column, definition in {
            'state': "TEXT NOT NULL DEFAULT 'active'", 'topic': 'INTEGER',
            'previous_id': 'INTEGER', 'event_time': 'TEXT', 'created': 'TEXT',
        }.items():
            db.execute(f'ALTER TABLE memories ADD COLUMN {column} {definition}')
        db.execute('UPDATE memories SET topic=id')
        db.execute('CREATE TABLE source_dependencies(source_id INTEGER NOT NULL,parent_id INTEGER NOT NULL,PRIMARY KEY(source_id,parent_id))')
        db.execute("INSERT OR IGNORE INTO profile VALUES('_memory_revision','0')")
