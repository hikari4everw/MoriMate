import { useCallback, useEffect, useState, type ReactNode } from 'react';

export type Session = { role: 'owner' | 'guest'; authenticated: boolean; guest?: { id: string; label: string } };

export function AccessGate({ base, children }: { base: string; children: (session: Session, expire: (reason: string) => void) => ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const bootstrap = useCallback(async () => {
    setPending(true); setError('');
    try {
      const response = await fetch(`${base}/api/session`);
      if (!response.ok) throw new Error('服务不可用，请确认主人已开启共享。');
      setSession(await response.json());
    } catch (reason) { setError(String(reason)); }
    finally { setPending(false); }
  }, [base]);
  useEffect(() => { void bootstrap(); }, [bootstrap]);
  const expire = useCallback((reason: string) => { setSession({ role: 'guest', authenticated: false }); setError(reason); }, []);
  if (session?.authenticated) return children(session, expire);
  const login = async () => {
    setPending(true); setError('');
    try {
      const response = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '登录失败');
      setPassword(''); setSession(data);
    } catch (reason) { setError(String(reason)); }
    finally { setPending(false); }
  };
  return <main className="companion access-page"><section className="access-card">
    <h1>Hiyori · 局域网共享</h1>
    {session ? <><p>输入主人提供的访问密码，进入你的访客空间。</p>
      <form onSubmit={event => { event.preventDefault(); void login(); }}><label>访问密码<input aria-label="访问密码" type="password" autoComplete="current-password" value={password} maxLength={128} onChange={event => setPassword(event.target.value)} /></label><button className="primary" disabled={pending || !password} type="submit">{pending ? '正在登录…' : '进入聊天'}</button></form>
      <p>聊天、记忆和人格提示词按浏览器独立保存。同一浏览器和地址可继续使用；换设备、清理浏览器数据或地址变化后，可能成为新访客。</p>
      <p>数据保存在主人电脑上，主人可以查看聊天和记忆并清理全部访客数据。访客无法使用屏幕感知；切换模型、思考档位和音色会影响所有在线用户。</p></> : <p>{pending ? '正在连接服务…' : '暂时无法连接服务。'}</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <button disabled={pending} onClick={() => void bootstrap()}>重新连接服务</button>
  </section></main>;
}
