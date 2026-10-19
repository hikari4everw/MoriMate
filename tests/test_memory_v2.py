from open_llm_vtuber.companion.memory import MemoryStore


def test_forget_retains_visible_history_but_excludes_only_dependent_context(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    tea = store.add_source('user', '我喜欢红茶')
    memory = store.remember('preference', '喜欢红茶', [tea], '喜欢红茶')
    store.add_source('assistant', '我记住红茶啦')
    weather = store.add_source('user', '今天下雨了')
    unrelated = store.add_source('assistant', '带把伞吧')
    store.forget(memory, mode='exclude')
    assert '红茶' in str(store.history_page())
    assert '红茶' not in str(store.sources())
    assert {s['id'] for s in store.sources()} == {weather, unrelated}
    assert store.remember('preference', '喜欢红茶', [tea], '喜欢红茶') is None


def test_erase_clears_related_text_and_speech_without_clearing_other_history(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    source = store.add_source('user', '敏感的约定')
    item = store.remember('fact', '敏感的约定', [source], '敏感的约定')
    store.add_source('assistant', '约好了', speech='約束だ')
    store.add_source('user', '无关的新事情')
    store.forget(item, mode='erase')
    assert '敏感' not in str(store.history_page())
    assert '約束' not in str([dict(row) for row in store.db.execute('SELECT * FROM sources')])
    assert '无关的新事情' in str(store.sources())


def test_explicit_change_preserves_past_and_conflict_requires_confirmation(tmp_path):
    from open_llm_vtuber.companion.memory_types import MemoryCandidate
    store = MemoryStore(tmp_path / 'm.db')
    a = store.add_source('user', '我平时不喝咖啡')
    old = store.remember('preference', '不喝咖啡', [a], '不喝咖啡')
    b = store.add_source('user', '最近改了，现在每天喝咖啡')
    new = store.record_memory(MemoryCandidate('preference', '现在每天喝咖啡', [b], '现在每天喝咖啡', previous_id=old, explicit_change=True))
    assert [m['id'] for m in store.memories()] == [new]
    versions = store.memories(include_history=True)
    assert {m['state'] for m in versions} == {'active', 'superseded'}
    c = store.add_source('user', '我不喜欢咖啡')
    pending = store.record_memory(MemoryCandidate('preference', '不喜欢咖啡', [c], '不喜欢咖啡', previous_id=new))
    assert pending not in {m['id'] for m in store.memories()}
    assert store.memories(include_history=True)[0]['state'] == 'pending'


def test_forget_invalidates_transitive_assistant_paraphrases(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    source = store.add_source('user', '我喜欢海边')
    item = store.remember('preference', '喜欢海边', [source], '喜欢海边')
    first = store.add_source('assistant', '记住了', dependencies=[source])
    store.add_source('user', '再聊聊')
    store.add_source('assistant', '你会喜欢海风', dependencies=[first])
    before = store.revision
    store.forget(item)
    assert store.revision > before
    assert '海风' not in str(store.sources())


def test_manual_correction_remains_authoritative_and_versioned(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    source = store.add_source('user', '喜欢红茶')
    item = store.remember('preference', '红茶', [source], '红茶')
    store.correct(item, '改成绿茶')
    assert store.memories()[0]['text'] == '改成绿茶'
    assert len(store.memories(include_history=True)) == 2
    assert store.sources() == []


def test_forgetting_manual_memory_invalidates_its_generated_replies(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    source = store.add_source('user', '原始资料')
    item = store.remember('fact', '原始资料', [source], '原始资料')
    revised = store.correct(item, '人工纠正')
    store.add_source('user', '你记得什么')
    store.add_source('assistant', '人工纠正', dependencies=[], memory_ids=[revised])
    store.forget(revised)
    assert '人工纠正' not in str(store.sources())


def test_forget_blocks_repeated_existing_evidence_and_invalidates_checkpoints(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    first = store.add_source('user', '我喜欢咖啡')
    item = store.remember('preference', '喜欢咖啡', [first], '喜欢咖啡')
    second = store.add_source('user', '补充一下，我喜欢咖啡')
    store.save_summary('daily', '咖啡爱好者', [second])
    store.forget(item)
    assert '咖啡' not in str(store.sources())
    assert store.checkpoints('daily') == []


def test_confirm_pending_change_preserves_previous_revision(tmp_path):
    from open_llm_vtuber.companion.memory_types import MemoryCandidate
    store = MemoryStore(tmp_path / 'm.db')
    a = store.add_source('user', '喜欢红茶')
    old = store.remember('preference', '喜欢红茶', [a], '红茶')
    b = store.add_source('user', '喜欢绿茶')
    pending = store.record_memory(MemoryCandidate('preference', '喜欢绿茶', [b], '绿茶', previous_id=old))
    store.confirm(pending)
    assert store.memories()[0]['id'] == pending
    assert store.memories(include_history=True)[-1]['state'] == 'superseded'


def test_clear_scene_invalidates_cross_scene_replies_but_preserves_unrelated_history(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    source = store.add_source('user', '门禁码秘密123', 'daily')
    item = store.remember('fact', '门禁码秘密123', [source], '秘密123')
    store.add_source('user', '帮我回忆', 'game:B')
    store.add_source('assistant', '门禁码秘密123', 'game:B', dependencies=[source], memory_ids=[item])
    store.add_source('user', '独立新事情', 'game:B')
    store.add_source('assistant', '无关回答', 'game:B', dependencies=[])
    store.clear_scene('daily')
    assert '秘密123' not in str(store.sources('game:B'))
    assert '无关回答' in str(store.sources('game:B'))
