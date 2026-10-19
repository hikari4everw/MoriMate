import { useEffect, useState } from 'react';
import { connectionSubmission, providerUrls, type CloudConnection } from './protocol';

const blank = (): CloudConnection => ({ id: '', name: '', provider: 'openai', model: '',
  base_url: providerUrls.openai, proxy: '', context_tokens: 32768, max_output_tokens: 8192,
  supports_vision: false, allow_guests: false, allow_images: false });

export function ModelSettings({ send, messages, connected, switching }: {
  send: (message: object) => void; messages: Record<string, any>; connected: boolean; switching: boolean;
}) {
  const [draft, setDraft] = useState<CloudConnection>(blank);
  const [key, setKey] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => { if (connected) { send({ type: 'cloud-list' }); send({ type: 'cloud-usage' }); } }, [connected, send]);
  useEffect(() => {
    if (messages['cloud-test-result']) setNotice(messages['cloud-test-result'].message);
  }, [messages['cloud-test-result']]);
  const items: CloudConnection[] = messages['cloud-connections']?.items || [];
  const change = (field: string, value: unknown) => setDraft(old => ({ ...old, [field]: value }));
  const disabled = !connected || switching;
  return <div className="settings scroll">
    <p>主人选择全机模型。云端会接收人格、相关聊天与记忆，已开启观察的 OCR 和后台整理也使用当前模型。语音在本机合成。</p>
    <button onClick={() => { setDraft(blank()); setKey(''); setNotice(''); }}>新增连接</button>
    {items.map(item => <article className="memory" key={item.id}>
      <strong>{item.name || item.model}</strong><small>{item.provider} · {item.has_key ? '已配置密钥' : '未配置密钥'} · {item.allow_guests ? '允许访客' : '仅主人'}</small>
      <button disabled={disabled} onClick={() => { const { has_key: _hasKey, ...editable } = item; setDraft(editable); setKey(''); }}>编辑</button>
      <button disabled={disabled || !item.has_key} onClick={() => send({ type: 'model-switch', model_key: `cloud:${item.id}` })}>使用此模型</button>
      <button disabled={disabled || !item.has_key} onClick={() => { setNotice('测试中，会发送一次固定短请求…'); send({ type: 'cloud-test', connection_id: item.id }); }}>测试连接</button>
      <button disabled={disabled} onClick={() => { if (window.confirm('删除此连接及钥匙串中的 API 密钥？')) send({ type: 'cloud-delete', connection_id: item.id }); }}>删除连接</button>
    </article>)}
    <form autoComplete="off" onSubmit={event => { event.preventDefault(); send(connectionSubmission(draft, key)); setKey(''); setNotice('已提交保存；结果见连接列表或错误提示。'); }}>
      <label>连接 ID<input required pattern="[a-zA-Z0-9_-]{1,64}" value={draft.id} onChange={event => change('id', event.target.value)} /></label>
      <label>名称<input value={draft.name} maxLength={100} onChange={event => change('name', event.target.value)} /></label>
      <label>提供方<select value={draft.provider} onChange={event => setDraft(old => ({ ...old, provider: event.target.value, base_url: providerUrls[event.target.value] }))}><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="gemini">Gemini</option><option value="compatible">自定义兼容接口</option></select></label>
      <label>Base URL<input required value={draft.base_url} onChange={event => change('base_url', event.target.value)} /></label>
      <label>模型名称<input required value={draft.model} maxLength={200} onChange={event => change('model', event.target.value)} /></label>
      <label>API Key（留空保留已存密钥）<input type="password" autoComplete="new-password" value={key} onChange={event => setKey(event.target.value)} /></label>
      <label>HTTP 代理（可留空）<input value={draft.proxy} placeholder="http://127.0.0.1:端口" onChange={event => change('proxy', event.target.value)} /></label>
      <label>上下文容量 token<input type="number" min={1280} value={draft.context_tokens} onChange={event => change('context_tokens', Number(event.target.value))} /></label>
      <label>最大输出 token<input type="number" min={256} value={draft.max_output_tokens} onChange={event => change('max_output_tokens', Number(event.target.value))} /></label>
      <label><input type="checkbox" checked={draft.supports_vision} onChange={event => change('supports_vision', event.target.checked)} /> 此模型支持图片</label>
      <label><input type="checkbox" checked={draft.allow_images} onChange={event => change('allow_images', event.target.checked)} /> 允许发送所选窗口截图（需支持图片并主动观察）</label>
      <label><input type="checkbox" checked={draft.allow_guests} onChange={event => change('allow_guests', event.target.checked)} /> 授权局域网访客使用我的 API 额度</label>
      <button className="primary" disabled={disabled}>保存连接</button>
    </form>
    {notice && <p role="status">{notice}</p>}
    <h3>最近用量</h3><p>包括聊天、主动评论、记忆整理和连接测试；中断时服务商可能仍计费。缺少 token 报告显示未知，以服务商账单为准。</p>
    <button onClick={() => send({ type: 'cloud-usage' })}>刷新用量</button>
    {(messages['cloud-usage']?.items || []).map((item: any) => <article key={item.id}><small>{item.created} · {item.space} · {item.purpose} · {item.status}</small><p>{item.usage ? JSON.stringify(item.usage) : 'token 用量未知'}</p></article>)}
  </div>;
}
