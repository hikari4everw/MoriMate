import { useEffect, useState } from 'react';

type PanelMessage = { type: string; [key: string]: any };
export type PanelInbox = Record<string, PanelMessage | undefined>;
type Send = (data: object) => void;
type Guest = { id: string; label: string; online: boolean; last_seen: string };

export function PersonaPanel({ send, messages, connected }: { send: Send; messages: PanelInbox; connected: boolean }) {
  const [text, setText] = useState('');
  const [saved, setSaved] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState(false);
  useEffect(() => { if (connected) send({ type: 'persona-get' }); }, [send, connected]);
  const persona = messages.persona;
  const error = messages.error;
  useEffect(() => {
    if (persona) { setText(persona.text); setSaved(persona.text); setLoaded(true); setPending(false); }
  }, [persona]);
  useEffect(() => { if (error) setPending(false); }, [error]);
  return <div className="settings scroll persona-panel">
    <h3>我的人格提示词</h3>
    <p>定制身份、性格和交流风格。只影响你自己的伙伴；中文显示、日语播报及输出格式由程序固定。</p>
    <label>人格提示词<textarea aria-label="人格提示词" value={text} maxLength={16000} disabled={!loaded || pending} onChange={event => setText(event.target.value)} /></label>
    <small>{text.length} / 16000 字</small>
    <p>保存会停止自己的当前回复和排队请求；聊天和记忆继续保留，新人格从下一回合生效。</p>
    <button className="primary" disabled={!connected || !loaded || pending || !text.trim() || text === saved} onClick={() => { setPending(true); send({ type: 'persona-set', text }); }}>{pending ? '正在保存…' : '保存人格'}</button>
    <button disabled={!connected || !loaded || pending} onClick={() => { if (window.confirm('恢复项目默认人格？聊天和记忆会保留。')) { setPending(true); send({ type: 'persona-reset' }); } }}>恢复默认</button>
    {loaded && !pending && text === saved && <small role="status">当前人格已保存</small>}
  </div>;
}

export function SharingPanel({ send, messages, connected }: { send: Send; messages: PanelInbox; connected: boolean }) {
  const [sharing, setSharing] = useState<PanelMessage | null>(null);
  const [address, setAddress] = useState('');
  const [password, setPassword] = useState('');
  const [guests, setGuests] = useState<Guest[]>([]);
  const [selected, setSelected] = useState('');
  const [history, setHistory] = useState<any[]>([]);
  const [memories, setMemories] = useState<any[]>([]);
  const [before, setBefore] = useState<number | null>(null);
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState(false);
  useEffect(() => { if (connected) { send({ type: 'sharing-status' }); send({ type: 'guests-list' }); } }, [send, connected]);
  const snapshot = messages['sharing-status'];
  const error = messages.error;
  const guestList = messages['guests-list'];
  const guestHistory = messages['guest-history'];
  const guestMemories = messages['guest-memories'];
  useEffect(() => {
    if (snapshot) {
      setSharing(snapshot); setPending(false);
      setAddress(old => snapshot.addresses?.some((item: any) => item.address === old) ? old : snapshot.address);
      if (snapshot.password_set) setPassword('');
      send({ type: 'guests-list' });
    }
  }, [snapshot, send]);
  useEffect(() => { if (error) setPending(false); }, [error]);
  useEffect(() => {
    if (guestList) {
      setGuests(guestList.items);
      if (!guestList.items.some((item: Guest) => item.id === selected)) { setSelected(''); setHistory([]); setMemories([]); setBefore(null); }
    }
  }, [guestList, selected]);
  useEffect(() => {
    if (guestHistory?.guest_id === selected) {
      setHistory(old => [...guestHistory.items, ...old.filter(row => !guestHistory.items.some((item: any) => item.id === row.id))]);
      setBefore(guestHistory.next_before);
    }
  }, [guestHistory, selected]);
  useEffect(() => { if (guestMemories?.guest_id === selected) setMemories(guestMemories.items); }, [guestMemories, selected]);
  const inspect = (guest: Guest) => {
    setSelected(guest.id); setHistory([]); setMemories([]); setBefore(null);
    send({ type: 'guest-history', guest_id: guest.id });
    send({ type: 'guest-memories', guest_id: guest.id });
  };
  const copyAddress = async () => {
    if (!sharing?.url) return;
    try { await navigator.clipboard.writeText(sharing.url); setNotice('地址已复制'); }
    catch { setNotice('复制不可用，请选中下面的地址手动复制'); }
  };
  return <div className="settings scroll sharing-panel">
    <h3>局域网共享</h3>
    <p>每次启动默认关闭。访客输入地址和访问密码后进入自己的空间，不能使用屏幕感知。</p>
    <label>访问密码<input aria-label="设置访问密码" type="password" autoComplete="new-password" value={password} minLength={8} maxLength={128} placeholder={sharing?.password_set ? '已设置；输入新密码可重设' : '首次开启前设置 8–128 字符密码'} onChange={event => setPassword(event.target.value)} /></label>
    <button disabled={!connected || pending || password.length < 8} onClick={() => { if (!sharing?.enabled || window.confirm('修改密码会断开所有访客，保留他们的数据。继续？')) { setPending(true); send({ type: 'sharing-password-set', password }); } }}>保存访问密码</button>
    <label>局域网地址<select aria-label="局域网地址" value={address} disabled={sharing?.enabled || pending} onChange={event => setAddress(event.target.value)}><option value="">选择网卡地址</option>{sharing?.addresses?.map((item: any) => <option key={item.address} value={item.address}>{item.label}</option>)}</select></label>
    {sharing && !sharing.addresses?.length && <p>未检测到可用的局域网 IPv4，请连接 Wi-Fi 或有线网络后重新打开共享页。</p>}
    <button className="primary" disabled={!connected || pending || (!sharing?.enabled && (!sharing?.password_set || !address))} onClick={() => { setPending(true); send(sharing?.enabled ? { type: 'sharing-disable' } : { type: 'sharing-enable', address }); }}>{pending ? '正在处理…' : sharing?.enabled ? '关闭共享并断开访客' : '开启局域网共享'}</button>
    <p role="status">{sharing?.enabled ? `共享已开启 · 在线访客 ${sharing.online} 人` : '共享已关闭'}</p>
    {sharing?.url && <><input aria-label="访客访问地址" readOnly value={sharing.url} onFocus={event => event.target.select()} /><button onClick={copyAddress}>复制访问地址</button></>}
    {notice && <p role="status">{notice}</p>}
    <p>使用局域网 HTTP；密码不会显示在地址中。访问密码忘记后可直接重设。</p>
    <hr /><h3>访客管理</h3>
    <button disabled={!connected} onClick={() => send({ type: 'guests-list' })}>刷新访客列表</button>
    <button disabled={!connected || !guests.length} onClick={() => { if (window.confirm('永久删除全部访客的聊天、记忆及自定义人格，并断开他们？此操作无法撤销。')) send({ type: 'guests-clear' }); }}>清空全部访客数据</button>
    {!guests.length && <p>还没有访客记录。</p>}
    {guests.map(guest => <article className={`guest-card ${selected === guest.id ? 'inspected' : ''}`} key={guest.id}>
      <strong>{guest.label} · {guest.online ? '在线' : '离线'}</strong><small>最近访问：{new Date(guest.last_seen).toLocaleString()}</small>
      <button onClick={() => inspect(guest)}>查看聊天和记忆</button>
      <button onClick={() => { if (window.confirm(`永久删除${guest.label}的全部数据并断开连接？此操作无法撤销。`)) send({ type: 'guest-delete', guest_id: guest.id }); }}>清除此访客</button>
    </article>)}
    {selected && <section className="guest-inspection"><h3>{guests.find(guest => guest.id === selected)?.label}的聊天</h3>
      <button onClick={() => { const guest = guests.find(item => item.id === selected); if (guest) inspect(guest); }}>刷新内容</button>
      {before && <button onClick={() => send({ type: 'guest-history', guest_id: selected, before })}>加载更早记录</button>}
      {!history.length && <p>暂无聊天记录。</p>}{history.map(row => <article key={row.id}><small>{row.kind === 'user' ? '访客' : '伙伴'} · {new Date(row.created).toLocaleString()}</small><p>{row.text}</p></article>)}
      <h3>长期记忆</h3>{!memories.length && <p>暂无长期记忆。</p>}{memories.map(row => <article key={row.id}><p>{row.text}</p><small>来源：{row.origin} · {row.evidence}</small></article>)}
    </section>}
  </div>;
}
