import { useEffect, useRef, useState } from 'react';
import { ChakraProvider, defaultSystem } from '@chakra-ui/react';
import { ModeProvider, useMode } from '../context/mode-context';
import { useLive2DModel } from '../hooks/canvas/use-live2d-model';
import { useLive2DResize } from '../hooks/canvas/use-live2d-resize';
import { LAppWavFileHandler } from '../../WebSDK/src/lappwavfilehandler';
import { audioManager } from '../utils/audio-manager';
import { TurnPlayer } from './turn-player';
import './companion.css';

const BASE = window.location.protocol === 'http:' && window.location.port !== '5173'
  ? window.location.origin : 'http://127.0.0.1:12393';
const modelInfo = { url: `${BASE}/live2d-models/Hiyori/Hiyori.model3.json`, kScale: 1,
  initialXshift: 0, initialYshift: 0, emotionMap: {}, pointerInteractive: true, scrollToResize: true };
type Region = [number, number, number, number];
type History = { id: number; kind: string; text: string };
type Memory = { id: number; kind: string; text: string; evidence: string; origin: string };
type WindowItem = { id: number; app: string; title: string };
type AudioPacket = { turn_id: number; sentence_id: number; audio: string | null; display_text: { text: string }; tts_text: string; expires_at_ms?: number; actions?: { expressions?: string[] } };
type VoiceProfile = { key: string; label: string; engine: string; engine_label: string };
type Status = { observing: boolean; muted: boolean; proactive: boolean; intensity: string; scene: string;
  voice_label?: string; model?: string; managed_models?: boolean; active_model_key?: string | null;
  target_model_key?: string | null; thinking_mode?: string; available_models?: { key: string; label: string }[];
  model_phase?: string; model_error?: string; model_recovery?: string; failed_model_key?: string | null;
  managed_voices?: boolean; available_voices?: VoiceProfile[]; active_voice_key?: string | null;
  target_voice_key?: string | null; failed_voice_key?: string | null; voice_phase?: string;
  voice_error?: string; voice_recovery?: string;
  supports_vision?: boolean; metrics: { first_audio_seconds: number }[]; summary?: { text: string } };

function Avatar() {
  const containerRef = useRef<HTMLDivElement>(null);
  const { canvasRef } = useLive2DResize({ containerRef, modelInfo });
  const { handlers } = useLive2DModel({ modelInfo, canvasRef });
  return <div className="avatar" ref={containerRef} {...handlers}><canvas id="canvas" ref={canvasRef} /></div>;
}

function Companion() {
  const { mode, setMode, isElectron } = useMode();
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState<Status>({ observing: false, muted: false, proactive: true, intensity: 'natural', scene: 'daily', metrics: [] });
  const [history, setHistory] = useState<History[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [windows, setWindows] = useState<WindowItem[]>([]);
  const [selected, setSelected] = useState('');
  const [preview, setPreview] = useState('');
  const [gameRegion, setGameRegion] = useState<Region | null>(null);
  const [dialogueRegion, setDialogueRegion] = useState<Region | null>(null);
  const [cropMode, setCropMode] = useState<'game' | 'dialogue'>('dialogue');
  const [scene, setScene] = useState('拔作岛·主存档');
  const [ocr, setOcr] = useState('');
  const [input, setInput] = useState('');
  const [subtitle, setSubtitle] = useState('一起聊会儿？');
  const [japanese, setJapanese] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState('');
  const [configPending, setConfigPending] = useState(false);
  const [voicePending, setVoicePending] = useState(false);
  const [tab, setTab] = useState('chat');
  const socket = useRef<WebSocket | null>(null);
  const player = useRef<TurnPlayer<AudioPacket> | null>(null);
  const silent = useRef(false);
  const muted = useRef(false);
  const cropStart = useRef<[number, number] | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const send = (data: object) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(data));
    else setError('本地服务未连接，请运行启动脚本。');
  };

  useEffect(() => {
    let disposed = false;
    let reconnect: ReturnType<typeof setTimeout>;
    let waitTimer: ReturnType<typeof setTimeout>;
    let textReceived = false;
    const clearWait = () => { clearTimeout(waitTimer); setWaiting(''); };
    let finishAudio: (() => void) | null = null;
    const stopAudio = () => {
      clearWait();
      audioManager.stopCurrentAudioAndLipSync();
      finishAudio?.();
      finishAudio = null;
      const model = (window as any).getLive2DManager?.()?.getModel(0);
      model?._motionManager?.stopAllMotions();
      model?.setExpression?.('neutral');
    };
    const queue = new TurnPlayer<AudioPacket>(async (packet, current) => {
      if (!current() || (packet.expires_at_ms && Date.now() >= packet.expires_at_ms)) return;
      setSubtitle(packet.display_text.text);
      setJapanese(packet.tts_text);
      const model = (window as any).getLive2DManager?.()?.getModel(0);
      model?.setExpression?.(packet.actions?.expressions?.[0] || 'neutral');
      if (!packet.audio || silent.current || muted.current) { clearWait(); return; }
      const url = `data:audio/wav;base64,${packet.audio}`;
      // A private WAV decoder per sentence prevents late loads from reviving canceled lip motion.
      const wav = new LAppWavFileHandler();
      await wav.loadWavFile(url);
      if (!current() || (packet.expires_at_ms && Date.now() >= packet.expires_at_ms)) { wav.releasePcmData(); return; }
      if (model) model._wavFileHandler = wav;
      await new Promise<void>((resolve, reject) => {
        const audio = new Audio(url);
        audioManager.setCurrentAudio(audio, model);
        const finish = () => {
          audioManager.clearCurrentAudio(audio);
          wav.releasePcmData();
          if (finishAudio === finish) finishAudio = null;
          resolve();
        };
        finishAudio = finish;
        audio.onended = finish;
        audio.onerror = () => { finish(); setError('声音播放失败，请检查输出设备。'); };
        audio.onplaying = () => {
          if (!current()) { audio.pause(); finish(); return; }
          wav._sampleOffset = 0;
          clearWait();
          wav._userTimeSeconds = 0;
          send({ type: 'audio-play-start', turn_id: packet.turn_id });
        };
        if (!current()) { finish(); return; }
        audio.play().catch(reason => { finish(); reject(reason); setError('播放被阻止，请点击界面后再发一句话。'); });
      });
    }, stopAudio, turn => {
      clearWait();
      setBusy(false);
      send({ type: 'frontend-playback-complete', turn_id: turn });
    }, reason => setError(String(reason)));
    player.current = queue;
    const connect = () => {
      const ws = new WebSocket(`${BASE.replace('http', 'ws')}/client-ws`);
      socket.current = ws;
      ws.onopen = () => { queue.resetSession(); setConnected(true); setError(''); };
      ws.onmessage = event => {
        if (socket.current !== ws) return;
        const data = JSON.parse(event.data);
        if (data.type === 'status') { setStatus(data); setConfigPending(false); setVoicePending(false); muted.current = data.muted; }
        if (data.type === 'history') setHistory(data.items.filter((i: History) => i.kind !== 'observation'));
        if (data.type === 'memories') setMemories(data.items);
        if (data.type === 'windows') setWindows(data.items);
        if (data.type === 'preview') { setPreview(data.image); setGameRegion(null); setDialogueRegion(null); }
        if (data.type === 'observation') setOcr(data.text);
        if (data.type === 'error' && (data.turn_id === undefined || queue.accepts(data.turn_id))) { setError(data.message); setConfigPending(false); setVoicePending(false); }
        if (data.type === 'memory-updated') send({ type: 'memories' });
        if (data.type === 'speech-stopped') { silent.current = true; queue.cancelPlayback(); }
        if (data.type === 'interrupt-signal') { queue.stop(); setBusy(false); }
        if (data.type === 'control' && data.text === 'conversation-chain-start' && queue.begin(data.turn_id)) {
          silent.current = false; setBusy(true); textReceived = false;
          waitTimer = setTimeout(() => setWaiting(textReceived ? '文字已显示，正在准备语音…' : '还在认真想，可以随时发新消息打断。'), data.wait_after_ms || 8000);
        }
        if (data.type === 'sentence' && queue.acceptSentence(data.turn_id, data.sentence_id, data.expires_at_ms)) {
          textReceived = true;
          setWaiting(value => value ? '文字已显示，正在准备语音…' : value);
          setHistory(items => [...items, { id: data.source_id, kind: 'assistant', text: data.zh }]);
          if (silent.current || muted.current) clearWait();
        }
        if (data.type === 'control' && data.text === 'conversation-chain-end') queue.end(data.turn_id);
        if (data.type === 'audio') {
          // A refreshed page may briefly be connected to the pre-upgrade backend.
          if (data.sentence_id === undefined && queue.accepts(data.turn_id)) setHistory(items => [...items, { id: Date.now(), kind: 'assistant', text: data.display_text.text }]);
          queue.enqueue(data.turn_id, data, data.sentence_id);
        }
      };
      ws.onclose = () => {
        if (socket.current !== ws) return;
        queue.stop(); setConnected(false); setBusy(false); setConfigPending(false); setVoicePending(false);
        setStatus(old => ({ ...old, observing: false }));
        if (!disposed) reconnect = setTimeout(connect, 2500);
      };
    };
    connect();
    return () => { disposed = true; clearTimeout(reconnect); clearTimeout(waitTimer); queue.stop(); socket.current?.close(); };
  }, []);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [history]);
  useEffect(() => { document.body.classList.toggle('pet-view', mode === 'pet'); }, [mode]);
  const stop = () => { player.current?.stop(); setBusy(false); send({ type: 'interrupt-signal' }); };
  const stopSpeech = () => { silent.current = true; player.current?.cancelPlayback(); send({ type: 'stop-speech' }); };
  const modelSwitching = configPending || ['starting', 'stopping', 'loading', 'recovering'].includes(status.model_phase || '');
  const voiceSwitching = voicePending || ['starting', 'stopping', 'loading', 'recovering'].includes(status.voice_phase || '');
  const switching = modelSwitching || voiceSwitching;
  const modelUnavailable = switching || status.model_phase === 'error';
  const changeModelConfig = (data: object) => {
    if (!connected || switching || !status.managed_models) return;
    player.current?.stop(); setBusy(false); setWaiting(''); setError(''); setConfigPending(true);
    send(data);
  };
  const modelLabel = (key?: string | null) => status.available_models?.find(item => item.key === key)?.label || key || '不可用';
  const phaseLabel: Record<string, string> = { starting: '正在启动', stopping: '正在停止旧模型', loading: '正在加载目标模型', recovering: '切换失败，正在恢复原模型', error: '模型不可用' };
  const profiles = status.available_voices || [];
  const selectedVoice = profiles.find(item => item.key === ((voiceSwitching ? status.target_voice_key : status.active_voice_key) || status.target_voice_key)) || profiles[0];
  const engines = [...new Map(profiles.map(item => [item.engine, item.engine_label])).entries()];
  const voicePhaseLabel: Record<string, string> = { starting: '正在启动语音', stopping: '正在停止旧语音', loading: '正在加载并验证语音', recovering: '正在回退 AivisSpeech', error: '语音不可用，继续文字模式' };
  const changeVoice = (key?: string) => {
    if (!key || !connected || switching || !status.managed_voices) return;
    player.current?.stop(); setBusy(false); setWaiting(''); setError(''); setVoicePending(true);
    send({ type: 'voice-switch', voice_key: key });
  };
  const submit = () => {
    const text = input.trim();
    if (!text || !connected || modelUnavailable) return;
    player.current?.stop();
    setHistory(items => [...items, { id: Date.now(), kind: 'user', text }]);
    send({ type: 'text-input', text }); setInput(''); setError('');
  };
  const settings = (values: object) => send({ type: 'settings', ...values });
  const point = (event: React.PointerEvent<HTMLDivElement>): [number, number] => {
    const box = event.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), Math.max(0, Math.min(1, (event.clientY - box.top) / box.height))];
  };
  const crop = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!cropStart.current) return;
    const [x, y] = point(event); const [a, b] = cropStart.current;
    const value: Region = [Math.min(x, a), Math.min(y, b), Math.abs(x - a), Math.abs(y - b)];
    if (cropMode === 'game') setGameRegion(value); else setDialogueRegion(value);
  };
  const renderRegion = (value: Region | null, className: string) => value && <div className={`crop ${className}`} style={{ left: `${value[0] * 100}%`, top: `${value[1] * 100}%`, width: `${value[2] * 100}%`, height: `${value[3] * 100}%` }} />;

  return <main className={`companion ${mode}`}>
    <header><strong>Hiyori <small>本地陪伴</small></strong><span className={connected ? 'online' : ''}>{connected ? busy ? waiting || '正在回应…' : '已连接' : '等待本地服务'}</span>
      {isElectron && <button onClick={() => setMode(mode === 'pet' ? 'window' : 'pet')}>{mode === 'pet' ? '展开' : '桌宠'}</button>}</header>
    <section className="stage"><Avatar /><div className="subtitle">{subtitle}<small>{japanese}</small></div></section>
    <section className="panel">
      <div className="model-controls" aria-label="模型设置">
        <label>模型<select aria-label="选择模型" value={(modelSwitching ? status.target_model_key : status.active_model_key) || ''} disabled={!connected || switching || !status.managed_models} onChange={e => changeModelConfig({ type: 'model-switch', model_key: e.target.value })}>
          {!status.managed_models && <option value="">外部服务 · {status.model || '当前模型'}</option>}
          {status.managed_models && !status.active_model_key && !switching && <option value="">请选择模型</option>}
          {status.available_models?.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select></label>
        <label>思考<select aria-label="选择思考档位" value={status.thinking_mode || 'off'} disabled={!connected || switching || !status.managed_models || status.active_model_key !== 'qwen38-rp'} onChange={e => changeModelConfig({ type: 'thinking-mode', thinking_mode: e.target.value })}>
          <option value="off">关闭</option><option value="limited">有限预算</option><option value="full">完整思考（可能长时间等待）</option>
        </select></label>
        <small role="status">{modelSwitching ? `${phaseLabel[status.model_phase || ''] || '正在更新配置'} · ${modelLabel(status.target_model_key)}` : status.managed_models ? `当前就绪：${modelLabel(status.active_model_key)}` : '外部模型服务；请用托管启动器启用切换'}</small>
        {status.model_error && <div role="alert" className="error">{status.model_error}<br />{status.model_recovery}<button disabled={!connected || switching} onClick={() => changeModelConfig({ type: 'model-switch', model_key: status.failed_model_key || status.target_model_key || '27b' })}>重试</button></div>}
      </div>
      <div className="model-controls" aria-label="语音设置">
        <label>语音引擎<select aria-label="选择语音引擎" value={selectedVoice?.engine || ''} disabled={!connected || switching || !status.managed_voices} onChange={event => changeVoice(profiles.find(item => item.engine === event.target.value)?.key)}>
          {!status.managed_voices && <option value="">外部语音服务</option>}
          {engines.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>角色音色<select aria-label="选择角色音色" value={selectedVoice?.key || ''} disabled={!connected || switching || !status.managed_voices} onChange={event => changeVoice(event.target.value)}>
          {!status.managed_voices && <option value="">{status.voice_label || '当前音色'}</option>}
          {profiles.filter(item => item.engine === selectedVoice?.engine).map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
        </select></label>
        <small role="status">{voiceSwitching ? `${voicePhaseLabel[status.voice_phase || ''] || '正在切换语音'} · ${selectedVoice?.label || ''}` : status.voice_phase === 'error' ? voicePhaseLabel.error : `当前声音：${status.voice_label || '本地语音'} · 自动语气`}</small>
        {status.voice_error && <div role="alert" className="error">{status.voice_error}<br />{status.voice_recovery}<button disabled={!connected || switching} onClick={() => changeVoice(status.failed_voice_key || status.target_voice_key || selectedVoice?.key)}>重试语音</button></div>}
      </div>
      <nav>{[['chat', '聊天'], ['observe', '观察'], ['memory', '记忆']].map(([key, label]) => <button key={key} className={tab === key ? 'selected' : ''} onClick={() => setTab(key)}>{label}</button>)}</nav>
      {tab === 'chat' && <div className="history">{history.map((item, i) => <article key={`${item.id}-${i}`} className={item.kind}><small>{item.kind === 'user' ? '你' : 'Hiyori'}</small><p>{item.text}</p></article>)}<div ref={endRef} /></div>}
      {tab === 'observe' && <div className="settings scroll">
        <p>只捕获你选择的窗口。首次使用需在 macOS「屏幕与系统音频录制」中授权启动终端或应用。</p>
        <button onClick={() => send({ type: 'windows' })}>刷新窗口列表</button>
        <select value={selected} onChange={e => { setSelected(e.target.value); setPreview(''); }}><option value="">选择虚拟机窗口</option>{windows.map(item => <option key={item.id} value={item.id}>{item.app} · {item.title || '(无标题)'}</option>)}</select>
        <button disabled={!selected} onClick={() => { stop(); send({ type: 'preview', window: Number(selected) }); }}>预览所选窗口</button>
        {preview && <><p>在预览中拖出矩形。蓝色为游戏区域，黄色为对白与说话人区域。未框选时使用完整窗口。</p>
          <select value={cropMode} onChange={e => setCropMode(e.target.value as 'game' | 'dialogue')}><option value="dialogue">框选对白＋说话人</option><option value="game">框选游戏画面</option></select>
          <div className="preview" onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); cropStart.current = point(e); }} onPointerMove={crop} onPointerUp={e => { crop(e); cropStart.current = null; }}><img src={preview} draggable={false} alt="所选窗口预览" />{renderRegion(gameRegion, 'game-crop')}{renderRegion(dialogueRegion, 'dialogue-crop')}</div>
          <button onClick={() => { setGameRegion(null); setDialogueRegion(null); }}>重置框选</button>
          <label>游戏／存档名称<input value={scene} maxLength={80} onChange={e => setScene(e.target.value)} /></label>
          <button className="primary" onClick={() => send({ type: 'observe-start', scene, game_region: gameRegion, dialogue_region: dialogueRegion })}>开始观察</button></>}
        <p>{status.observing ? '正在观察' : '观察已停止'} · {status.scene}</p>
        <button disabled={!status.observing || modelUnavailable || status.supports_vision === false} onClick={() => send({ type: 'look' })}>看一下当前画面</button>
        <button onClick={() => send({ type: 'daily' })}>回到日常聊天</button>
        <label>主动发言频率<select value={status.intensity} onChange={e => settings({ intensity: e.target.value })}><option value="quiet">安静</option><option value="natural">自然</option><option value="active">活跃</option></select></label>
        <h3>最近读到的对白</h3><pre>{ocr || '尚未识别到稳定对白'}</pre>
        {status.summary && <><h3>剧情摘要</h3><p>{status.summary.text}</p></>}
      </div>}
      {tab === 'memory' && <div className="settings scroll"><p>记忆只从明确来源整理。纠正或删除会同时作废关联摘要与历史，避免旧内容重新出现。</p><button onClick={() => send({ type: 'memories' })}>刷新记忆</button>{memories.length === 0 && <p>还没有长期记忆；聊几轮后会在空闲时整理。</p>}{memories.map(memory => <MemoryCard key={`${memory.id}-${memory.text}`} memory={memory} send={send} />)}<hr /><button onClick={() => { if (window.confirm('清除当前场景的对话、摘要和关联记忆？')) send({ type: 'scene-clear' }); }}>清除当前场景记录</button></div>}
      <div className="controls">
        <button onClick={() => { send({ type: 'observe-stop' }); }} disabled={!status.observing}>停止观察</button>
        <button onClick={() => settings({ proactive: !status.proactive })}>{status.proactive ? '禁止主动发言' : '允许主动发言'}</button>
        <button onClick={() => { if (!status.muted) { silent.current = true; player.current?.cancelPlayback(); } settings({ muted: !status.muted }); }}>{status.muted ? '恢复声音' : '静音'}</button>
        <button onClick={stopSpeech}>停止当前语音</button>
      </div>
      {error && <div role="alert" className="error" onClick={() => setError('')}>{error}</div>}
      <form onSubmit={e => { e.preventDefault(); submit(); }}><input aria-label="输入中文消息" value={input} maxLength={4000} onChange={e => setInput(e.target.value)} placeholder="想聊点什么？" /><button className="primary" disabled={!connected || modelUnavailable || !input.trim()}>发送</button></form>
      <footer>中文显示 · 日语发声 · {status.voice_label || '本地语音'}{status.metrics.length > 0 && ` · 最近首句 ${status.metrics.at(-1)?.first_audio_seconds}s`}</footer>
    </section>
  </main>;
}

function MemoryCard({ memory, send }: { memory: Memory; send: (data: object) => void }) {
  const [text, setText] = useState(memory.text);
  return <article className="memory"><textarea value={text} maxLength={600} onChange={e => setText(e.target.value)} /><small>来源：{memory.origin} · {memory.evidence}</small><button disabled={!text.trim() || text === memory.text} onClick={() => send({ type: 'memory-edit', id: memory.id, text })}>保存纠正</button><button onClick={() => send({ type: 'memory-delete', id: memory.id })}>删除</button></article>;
}

export default function LocalCompanion() {
  return <ChakraProvider value={defaultSystem}><ModeProvider><Companion /></ModeProvider></ChakraProvider>;
}
