import { useEffect, useRef, useState } from 'react';
import { ChakraProvider, defaultSystem } from '@chakra-ui/react';
import { ModeProvider, useMode } from '../context/mode-context';
import { useLive2DModel } from '../hooks/canvas/use-live2d-model';
import { useLive2DResize } from '../hooks/canvas/use-live2d-resize';
import { LAppWavFileHandler } from '../../WebSDK/src/lappwavfilehandler';
import { audioManager } from '../utils/audio-manager';
import { TurnPlayer } from './turn-player';
import './companion.css';

const BASE = 'http://127.0.0.1:12393';
const modelInfo = { url: `${BASE}/live2d-models/Hiyori/Hiyori.model3.json`, kScale: 1,
  initialXshift: 0, initialYshift: 0, emotionMap: {}, pointerInteractive: true, scrollToResize: true };
type Region = [number, number, number, number];
type History = { id: number; kind: string; text: string };
type Memory = { id: number; kind: string; text: string; evidence: string; origin: string };
type WindowItem = { id: number; app: string; title: string };
type AudioPacket = { turn_id: number; audio: string | null; display_text: { text: string }; tts_text: string; actions?: { expressions?: string[] } };
type Status = { observing: boolean; muted: boolean; proactive: boolean; intensity: string; scene: string;
  voice_label?: string; model?: string; metrics: { first_audio_seconds: number }[]; summary?: { text: string } };

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
  const [tab, setTab] = useState('chat');
  const socket = useRef<WebSocket | null>(null);
  const player = useRef<TurnPlayer<AudioPacket> | null>(null);
  const cropStart = useRef<[number, number] | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const send = (data: object) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(data));
    else setError('本地服务未连接，请运行启动脚本。');
  };

  useEffect(() => {
    let disposed = false;
    let reconnect: ReturnType<typeof setTimeout>;
    let finishAudio: (() => void) | null = null;
    const stopAudio = () => {
      audioManager.stopCurrentAudioAndLipSync();
      finishAudio?.();
      finishAudio = null;
      const model = (window as any).getLive2DManager?.()?.getModel(0);
      model?._motionManager?.stopAllMotions();
      model?.setExpression?.('neutral');
    };
    const queue = new TurnPlayer<AudioPacket>(async (packet, current) => {
      if (!current()) return;
      setSubtitle(packet.display_text.text);
      setJapanese(packet.tts_text);
      setHistory(items => [...items, { id: Date.now(), kind: 'assistant', text: packet.display_text.text }]);
      const model = (window as any).getLive2DManager?.()?.getModel(0);
      model?.setExpression?.(packet.actions?.expressions?.[0] || 'neutral');
      if (!packet.audio) return;
      const url = `data:audio/wav;base64,${packet.audio}`;
      // A private WAV decoder per sentence prevents late loads from reviving canceled lip motion.
      const wav = new LAppWavFileHandler();
      await wav.loadWavFile(url);
      if (!current()) { wav.releasePcmData(); return; }
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
          wav._userTimeSeconds = 0;
          send({ type: 'audio-play-start', turn_id: packet.turn_id });
        };
        if (!current()) { finish(); return; }
        audio.play().catch(reason => { finish(); reject(reason); setError('播放被阻止，请点击界面后再发一句话。'); });
      });
    }, stopAudio, turn => {
      setBusy(false);
      send({ type: 'frontend-playback-complete', turn_id: turn });
    }, reason => setError(String(reason)));
    player.current = queue;
    const connect = () => {
      const ws = new WebSocket(`${BASE.replace('http', 'ws')}/client-ws`);
      socket.current = ws;
      ws.onopen = () => { setConnected(true); setError(''); };
      ws.onmessage = event => {
        const data = JSON.parse(event.data);
        if (data.type === 'status') setStatus(data);
        if (data.type === 'history') setHistory(data.items.filter((i: History) => i.kind !== 'observation'));
        if (data.type === 'memories') setMemories(data.items);
        if (data.type === 'windows') setWindows(data.items);
        if (data.type === 'preview') { setPreview(data.image); setGameRegion(null); setDialogueRegion(null); }
        if (data.type === 'observation') setOcr(data.text);
        if (data.type === 'error') setError(data.message);
        if (data.type === 'memory-updated') send({ type: 'memories' });
        if (data.type === 'interrupt-signal') { queue.stop(); setBusy(false); }
        if (data.type === 'control' && data.text === 'conversation-chain-start') { queue.begin(data.turn_id); setBusy(true); }
        if (data.type === 'control' && data.text === 'conversation-chain-end') queue.end(data.turn_id);
        if (data.type === 'audio') queue.enqueue(data.turn_id, data);
      };
      ws.onclose = () => {
        queue.stop(); setConnected(false); setBusy(false);
        setStatus(old => ({ ...old, observing: false }));
        if (!disposed) reconnect = setTimeout(connect, 2500);
      };
    };
    connect();
    return () => { disposed = true; clearTimeout(reconnect); queue.stop(); socket.current?.close(); };
  }, []);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [history]);
  useEffect(() => { document.body.classList.toggle('pet-view', mode === 'pet'); }, [mode]);
  const stop = () => { player.current?.stop(); setBusy(false); send({ type: 'interrupt-signal' }); };
  const submit = () => {
    const text = input.trim();
    if (!text || !connected) return;
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
    <header><strong>Hiyori <small>本地陪伴</small></strong><span className={connected ? 'online' : ''}>{connected ? busy ? '正在回应…' : '已连接' : '等待本地服务'}</span>
      {isElectron && <button onClick={() => setMode(mode === 'pet' ? 'window' : 'pet')}>{mode === 'pet' ? '展开' : '桌宠'}</button>}</header>
    <section className="stage"><Avatar /><div className="subtitle">{subtitle}<small>{japanese}</small></div></section>
    <section className="panel">
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
        <button disabled={!status.observing} onClick={() => send({ type: 'look' })}>看一下当前画面</button>
        <button onClick={() => send({ type: 'daily' })}>回到日常聊天</button>
        <label>主动发言频率<select value={status.intensity} onChange={e => settings({ intensity: e.target.value })}><option value="quiet">安静</option><option value="natural">自然</option><option value="active">活跃</option></select></label>
        <h3>最近读到的对白</h3><pre>{ocr || '尚未识别到稳定对白'}</pre>
        {status.summary && <><h3>剧情摘要</h3><p>{status.summary.text}</p></>}
      </div>}
      {tab === 'memory' && <div className="settings scroll"><p>记忆只从明确来源整理。纠正或删除会同时作废关联摘要与历史，避免旧内容重新出现。</p><button onClick={() => send({ type: 'memories' })}>刷新记忆</button>{memories.length === 0 && <p>还没有长期记忆；聊几轮后会在空闲时整理。</p>}{memories.map(memory => <MemoryCard key={`${memory.id}-${memory.text}`} memory={memory} send={send} />)}<hr /><button onClick={() => { if (window.confirm('清除当前场景的对话、摘要和关联记忆？')) send({ type: 'scene-clear' }); }}>清除当前场景记录</button></div>}
      <div className="controls">
        <button onClick={() => { player.current?.stop(); send({ type: 'observe-stop' }); }} disabled={!status.observing}>停止观察</button>
        <button onClick={() => settings({ proactive: !status.proactive })}>{status.proactive ? '禁止主动发言' : '允许主动发言'}</button>
        <button onClick={() => { if (!status.muted) player.current?.stop(); settings({ muted: !status.muted }); }}>{status.muted ? '恢复声音' : '静音'}</button>
        <button onClick={stop}>停止当前语音</button>
      </div>
      {error && <div role="alert" className="error" onClick={() => setError('')}>{error}</div>}
      <form onSubmit={e => { e.preventDefault(); submit(); }}><input aria-label="输入中文消息" value={input} maxLength={4000} onChange={e => setInput(e.target.value)} placeholder="想聊点什么？" /><button className="primary" disabled={!connected || !input.trim()}>发送</button></form>
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
