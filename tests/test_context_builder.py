from open_llm_vtuber.companion.memory import MemoryStore


def builder(store):
    from open_llm_vtuber.companion.context_builder import ContextBuilder
    return ContextBuilder(store)


def request(query='红茶', scene='daily', budget=8000):
    from open_llm_vtuber.companion.context_builder import ContextRequest
    return ContextRequest(query=query, scene=scene, persona='人格', format_prompt='输出双语', input_budget=budget)


def test_filters_scene_before_ranking_and_retrieves_older_relevant_memory(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    old = store.add_source('observation', '红茶杯是小明留下的', 'game:A')
    item = store.remember('experience', '小明留下红茶杯', [old], '红茶杯')
    for i in range(100):
        source = store.add_source('observation', f'另一个世界事件{i}', 'game:B')
        store.remember('experience', f'另一个世界事件{i}', [source], f'事件{i}')
    bundle = builder(store).build(request('红茶杯谁留下的', 'game:A'))
    assert bundle.memory_ids == [item]
    assert '另一个世界' not in str(bundle.messages)
    assert old in bundle.source_ids
    assert '红茶杯' in bundle.trace['memories'][0]['evidence']


def test_irrelevant_memory_not_injected_and_complete_turns_are_preserved(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    source = store.add_source('user', '喜欢足球')
    store.remember('preference', '喜欢足球', [source], '足球')
    for i in range(12):
        store.add_source('user', f'第{i}次聊天')
        store.add_source('assistant', f'回复{i}第一句', speech='一。')
        store.add_source('assistant', f'回复{i}第二句', speech='二。')
    bundle = builder(store).build(request('今天画画'))
    assert bundle.memory_ids == []
    assert '第1次聊天' not in str(bundle.messages)
    assert '第2次聊天' in str(bundle.messages)
    assert '回复11第一句' in str(bundle.messages) and '回复11第二句' in str(bundle.messages)


def test_budget_keeps_current_input_and_drops_whole_old_turns(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    for i in range(10):
        store.add_source('user', f'{i}前缀' + '长' * 100)
        store.add_source('assistant', f'{i}回答' + '回' * 100, speech='日本語。')
    store.add_source('user', '当前必须保留')
    bundle = builder(store).build(request('当前必须保留', budget=700))
    assert '当前必须保留' in str(bundle.messages)
    assert bundle.trace['estimated_tokens'] <= 700
    assert bundle.trace['dropped_turns'] > 0


def test_memory_provenance_is_rechecked_after_forget(tmp_path):
    store = MemoryStore(tmp_path / 'm.db')
    s = store.add_source('user', '喜欢红茶')
    item = store.remember('preference', '喜欢红茶', [s], '红茶')
    bundle = builder(store).build(request())
    assert bundle.is_current(store)
    store.forget(item)
    assert not bundle.is_current(store)
    assert '红茶' not in str(builder(store).build(request('你好')).messages)
