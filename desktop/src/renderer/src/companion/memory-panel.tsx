import { useState } from 'react';

type Item = { id: number; text: string; kind: string; evidence: string; origin: string; state?: string; created?: string; previous_id?: number };

function Card({ item, send }: { item: Item; send: (message: object) => void }) {
  const [text, setText] = useState(item.text);
  const labels: Record<string, string> = { active: '当前', superseded: '过去版本', pending: '待确认冲突', excluded: '已停止使用' };
  return <article className="memory">
    <small>{labels[item.state || 'active']} · {item.kind} · {item.created || '时间未记录'}</small>
    <textarea aria-label={`记忆 ${item.id}`} value={text} maxLength={600} onChange={event => setText(event.target.value)} />
    <small>来源：{item.origin} · {item.evidence || '文本已删除'}{item.previous_id ? ` · 前版本 ${item.previous_id}` : ''}</small>
    {item.state !== 'excluded' && item.state !== 'superseded' && <><button disabled={!text.trim() || text === item.text} onClick={() => send({ type: 'memory-edit', id: item.id, text })}>保存纠正</button>
      {item.state === 'pending' && <button onClick={() => send({ type: 'memory-confirm', id: item.id })}>确认此变化</button>}
      <button onClick={() => send({ type: 'memory-delete', id: item.id, mode: 'exclude' })}>停止使用</button></>}
    <button onClick={() => { if (window.confirm('彻底删除这条记忆及相关来源和派生文本？无关历史保留。')) send({ type: 'memory-delete', id: item.id, mode: 'erase' }); }}>彻底删除</button>
  </article>;
}

export function MemoryPanel({ send, messages, memories }: { send: (message: object) => void; messages: Record<string, any>; memories: Item[] }) {
  const [view, setView] = useState('active');
  const trace = messages['context-trace'];
  const archive = messages['memory-archive'];
  const history = messages['memory-history'];
  return <div className="settings scroll">
    <p>新记忆从升级后建立。明确事实自动整理，改口保留变化；停止使用保留可查看原文，但不再参与回复与整理。</p>
    <div>{[['active', '当前记忆'], ['changes', '变化记录'], ['trace', '本轮引用'], ['archive', '旧资料归档']].map(([key, label]) => <button key={key} className={view === key ? 'selected' : ''} onClick={() => { setView(key); send({ type: key === 'archive' ? 'memory-archive' : 'memories' }); }}>{label}</button>)}</div>
    {view === 'active' && <><button onClick={() => send({ type: 'memories' })}>刷新记忆</button>{!memories.length && <p>还没有新记忆；聊几轮后会在空闲时整理。</p>}{memories.map(item => <Card key={`${item.id}-${item.text}`} item={item} send={send} />)}</>}
    {view === 'changes' && <>{(history?.items || []).map((item: Item) => <Card key={`${item.id}-${item.text}`} item={item} send={send} />)}<h3>当前场景摘要检查点</h3>{(history?.checkpoints || []).map((item: any) => <article key={item.id}><small>{item.created}</small><p>{item.text}</p></article>)}</>}
    {view === 'trace' && (trace ? <><p>最近一轮 · 估算 {trace.estimated_tokens}/{trace.budget} token · 裁去 {trace.dropped_turns} 个旧回合 · {trace.summary_used ? '使用了摘要' : '未使用摘要'}</p>{trace.memories.map((item: any) => <article className="memory" key={item.id}><p>{item.text}</p><small>{item.reason} · {item.created || ''} · 来源 {item.source_ids.join(', ')}</small><p>证据：{item.evidence}</p></article>)}<small>上下文来源 ID：{trace.source_ids.join(', ')}。这是本地估算，实际用量见云模型页。</small></> : <p>尚无本轮引用；修改或遗忘记忆后会清除旧引用视图。</p>)}
    {view === 'archive' && <><p>升级前的资料仅供查看，不进入新对话、检索或摘要重建。</p>{archive && <><h3>旧长期记忆</h3>{archive.memories.map((item: Item) => <article className="memory" key={item.id}><p>{item.text}</p><small>{item.evidence}</small></article>)}<h3>旧摘要</h3>{archive.summaries.map((item: any) => <article key={item.scene}><small>{item.scene}</small><p>{item.text}</p></article>)}<h3>旧聊天</h3>{archive.sources.map((item: any) => <article key={item.id}><small>{item.created} · {item.kind}</small><p>{item.text}</p></article>)}{archive.next_before && <button onClick={() => send({ type: 'memory-archive', before: archive.next_before })}>查看更早一页</button>}</>}</>}
    {history?.maintenance_error && <p role="alert">记忆整理已暂停：{history.maintenance_error}</p>}
    <button onClick={() => send({ type: 'memory-retry' })}>重试后台整理</button>
    <button onClick={() => { if (window.confirm('仅用升级后仍有效的来源重建当前场景摘要？会使用当前模型，云端可能收费。')) send({ type: 'memory-rebuild' }); }}>重建当前场景摘要</button>
    <hr /><button onClick={() => { if (window.confirm('清除当前场景的新对话、摘要和关联记忆？')) send({ type: 'scene-clear' }); }}>清除当前场景新记录</button>
  </div>;
}
