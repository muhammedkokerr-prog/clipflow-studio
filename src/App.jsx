import { useEffect, useRef, useState } from 'react';
import {
  ArrowDown, ArrowUp, AudioLines, Check, ChevronDown, Clapperboard, Download,
  FileAudio2, FileVideo2, GripVertical, LoaderCircle, Music2, Plus, Share2,
  Sparkles, Trash2, Upload, Volume2, X,
} from 'lucide-react';

const API = import.meta.env.VITE_API_URL || ''
const PROJECT_KEY = 'clipflow-project-v1';
const JOB_KEY = 'clipflow-recent-job-v1';
const languageNames = { tr: 'Türkçe', en: 'İngilizce', es: 'İspanyolca', fr: 'Fransızca' };
const audioTypes = { mp3: 'audio/mpeg', wav: 'audio/wav', m4a: 'audio/mp4', aac: 'audio/aac', ogg: 'audio/ogg' };
const audioLabels = { music: 'Müzik', voiceover: 'Seslendirme' };

function audioContentType(file) {
  const extension = file.name.split('.').pop()?.toLowerCase();
  return audioTypes[extension] || file.type;
}

function formatTime(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.floor(seconds % 60);
  return `${minutes}:${String(remaining).padStart(2, '0')}`;
}

function getDuration(file, url) {
  return new Promise((resolve) => {
    const media = document.createElement(file.type.startsWith('video/') ? 'video' : 'audio');
    media.preload = 'metadata';
    media.onloadedmetadata = () => resolve(media.duration || 0);
    media.onerror = () => resolve(0);
    media.src = url;
  });
}

async function responseJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'İstek tamamlanamadı.');
  return data;
}

export default function App() {
  const [clips, setClips] = useState([]);
  const [music, setMusic] = useState(null);
  const [voiceover, setVoiceover] = useState(null);
  const [project, setProject] = useState(null);
  const [job, setJob] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [outputUrl, setOutputUrl] = useState('');
  const [dragIndex, setDragIndex] = useState(null);
  const objectUrls = useRef(new Set());
  const [options, setOptions] = useState({
    fit_mode: 'crop', keep_clip_audio: true, music_start: 0, music_volume: 0.2, music_loop: false,
    voiceover_start: 0, voiceover_volume: 1, voiceover_loop: false, subtitle_source: 'none',
    subtitle_language: 'tr', subtitle_style: { font_name: 'Liberation Sans', font_size: 48, color: 'white', background: true, position: 2 },
  });
  const clipInput = useRef(null);
  const musicInput = useRef(null);
  const voiceInput = useRef(null);
  const totalDuration = clips.reduce((sum, clip) => sum + clip.duration, 0);

  const createObjectUrl = (blob) => {
    const url = URL.createObjectURL(blob);
    objectUrls.current.add(url);
    return url;
  };

  const releaseObjectUrl = (url) => {
    URL.revokeObjectURL(url);
    objectUrls.current.delete(url);
  };

  useEffect(() => {
    let active = true;
    const savedProject = localStorage.getItem(PROJECT_KEY);
    const savedJob = localStorage.getItem(JOB_KEY);
    const bootstrap = async () => {
      try {
        let current = savedProject ? JSON.parse(savedProject) : null;
        if (!current?.id || !current?.token) {
          current = await responseJson(await fetch(`${API}/api/projects`, { method: 'POST' }));
          localStorage.setItem(PROJECT_KEY, JSON.stringify(current));
        }
        if (active) setProject(current);
        if (savedJob && current) {
          const recent = JSON.parse(savedJob);
          if (recent.projectId === current.id) {
            const status = await responseJson(await fetch(`${API}/api/projects/${current.id}/jobs/${recent.id}`, {
              headers: { Authorization: `Bearer ${current.token}` },
            }));
            if (active) setJob(status);
          }
        }
      } catch (reason) {
        if (active) setError(reason.message);
      }
    };
    bootstrap();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!job || !project || !['queued', 'processing'].includes(job.status)) return undefined;
    const poll = async () => {
      try {
        const status = await responseJson(await fetch(`${API}/api/projects/${project.id}/jobs/${job.id}`, {
          headers: { Authorization: `Bearer ${project.token}` },
        }));
        setJob(status);
      } catch (reason) { setError(reason.message); }
    };
    const timer = window.setInterval(poll, 2500);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status, project]);

  useEffect(() => {
    if (job?.status !== 'completed' || !project || outputUrl) return undefined;
    let active = true;
    fetch(`${API}/api/projects/${project.id}/jobs/${job.id}/output`, {
      headers: { Authorization: `Bearer ${project.token}` },
    }).then((response) => {
      if (!response.ok) throw new Error('Hazır video indirilemedi.');
      return response.blob();
    }).then((blob) => {
      if (active) setOutputUrl(URL.createObjectURL(blob));
    }).catch((reason) => setError(reason.message));
    return () => { active = false; };
  }, [job?.status, project, outputUrl]);

  useEffect(() => () => objectUrls.current.forEach((url) => URL.revokeObjectURL(url)), []);

  const addFiles = async (files) => {
    const selected = Array.from(files).filter((file) => file.type.startsWith('video/'));
    const additions = await Promise.all(selected.map(async (file) => {
      const url = createObjectUrl(file);
      return { id: crypto.randomUUID(), file, url, duration: await getDuration(file, url), assetId: null };
    }));
    setClips((current) => [...current, ...additions]);
    setError('');
  };

  const setAudio = async (file, kind) => {
    if (!file) return;
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!audioTypes[extension]) { setError('MP3, WAV, M4A, AAC veya OGG ses dosyası seçin.'); return; }
    const url = createObjectUrl(file);
    const setter = kind === 'music' ? setMusic : setVoiceover;
    const item = { file, url, duration: 0, assetId: null, uploadStatus: 'reading', uploadPercent: 0 };
    setter((current) => { if (current?.url) releaseObjectUrl(current.url); return item; });
    try {
      const duration = await getDuration(file, url);
      if (!Number.isFinite(duration) || duration <= 0) throw new Error('Ses dosyasının süresi okunamadı.');
      setter((current) => current?.file === file ? { ...current, duration, uploadStatus: 'pending' } : current);
      setError('');
    } catch (reason) {
      setter((current) => current?.file === file ? { ...current, uploadStatus: 'invalid' } : current);
      setError(`${audioLabels[kind]} dosyası okunamadı: ${reason.message}`);
    }
  };

  const uploadFile = async (file, kind, progress, duration = 0) => {
    const contentType = kind === 'clip' ? file.type : audioContentType(file);
    const uploadFileValue = contentType === file.type ? file : new File([file], file.name, { type: contentType });
    if (project.direct_upload) {
      const reservation = await responseJson(await fetch(`${API}/api/projects/${project.id}/uploads/presign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${project.token}` },
        body: JSON.stringify({ kind, name: file.name, content_type: contentType, size: file.size, duration }),
      }));
      await new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open('POST', reservation.url);
        request.upload.onprogress = (event) => { if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100)); };
        request.onload = () => request.status >= 200 && request.status < 300 ? resolve() : reject(new Error('Dosya bulut depolamaya yüklenemedi.'));
        request.onerror = () => reject(new Error('Dosya yüklenirken bağlantı kesildi.'));
        const form = new FormData();
        Object.entries(reservation.fields).forEach(([name, value]) => form.append(name, value));
        form.append('file', uploadFileValue);
        request.send(form);
      });
      return responseJson(await fetch(`${API}/api/projects/${project.id}/uploads/${reservation.id}/complete`, {
        method: 'POST', headers: { Authorization: `Bearer ${project.token}` },
      }));
    }
    return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', `${API}/api/projects/${project.id}/assets?kind=${kind}`);
    request.setRequestHeader('Authorization', `Bearer ${project.token}`);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100));
    };
    request.onload = () => {
      let result = {};
      try { result = JSON.parse(request.responseText); } catch { /* API response is not JSON */ }
      if (request.status >= 200 && request.status < 300) resolve(result);
      else reject(new Error(result.detail || 'Dosya yüklenemedi.'));
    };
    request.onerror = () => reject(new Error('Dosya yüklenirken bağlantı kesildi.'));
    const body = new FormData();
    body.append('file', uploadFileValue);
    request.send(body);
    });
  };

  const uploadAudio = async (item, kind, progress) => {
    const setter = kind === 'music' ? setMusic : setVoiceover;
    const update = (patch) => setter((current) => current?.file === item.file ? { ...current, ...patch } : current);
    update({ uploadStatus: 'uploading', uploadPercent: 0 });
    try {
      const asset = await uploadFile(item.file, kind, (percent) => {
        update({ uploadPercent: percent });
        progress(percent);
      }, item.duration);
      if (!asset?.id) throw new Error('Sunucu dosya için asset kimliği döndürmedi.');
      update({ assetId: asset.id, uploadStatus: 'uploaded', uploadPercent: 100 });
      return { ...item, assetId: asset.id, uploadStatus: 'uploaded', uploadPercent: 100 };
    } catch (reason) {
      update({ assetId: null, uploadStatus: 'failed' });
      throw new Error(`${audioLabels[kind]} "${item.file.name}" yüklenemedi: ${reason.message}`);
    }
  };

  const renderVideo = async () => {
    if (!project) { setError('Proje hazırlanıyor; birkaç saniye sonra tekrar deneyin.'); return; }
    if (!clips.length) { setError('Önce en az bir video klip ekleyin.'); return; }
    if (options.subtitle_source === 'voiceover' && !voiceover) {
      setError('Seslendirme altyazısı için önce Seslendirme alanına bir ses dosyası ekleyin.');
      return;
    }
    if ((music && (!music.duration || music.uploadStatus === 'reading' || music.uploadStatus === 'invalid'))
      || (voiceover && (!voiceover.duration || voiceover.uploadStatus === 'reading' || voiceover.uploadStatus === 'invalid'))) {
      setError('Seçilen ses dosyasının bilgileri henüz okunamadı; dosyayı yeniden seçin. Sessiz render başlatılmadı.');
      return;
    }
    setBusy(true); setError(''); setOutputUrl(''); setJob(null);
    try {
      let complete = 0;
      const total = clips.length + Number(Boolean(music)) + Number(Boolean(voiceover));
      setUploadProgress({ done: 0, total, percent: 0 });
      const progress = (percent) => setUploadProgress((current) => ({ ...current, percent }));
      const uploadedClips = [];
      for (const clip of clips) {
        const asset = await uploadFile(clip.file, 'clip', progress, clip.duration);
        if (!asset?.id) throw new Error(`"${clip.file.name}" klibi için sunucu asset kimliği döndürmedi.`);
        uploadedClips.push({ ...clip, assetId: asset.id });
        complete += 1; setUploadProgress({ done: complete, total, percent: 0 });
      }
      let uploadedMusic = music;
      if (music) { uploadedMusic = await uploadAudio(music, 'music', progress); complete += 1; setUploadProgress({ done: complete, total, percent: 0 }); }
      let uploadedVoice = voiceover;
      if (voiceover) { uploadedVoice = await uploadAudio(voiceover, 'voiceover', progress); complete += 1; setUploadProgress({ done: complete, total, percent: 0 }); }
      setClips(uploadedClips); setMusic(uploadedMusic); setVoiceover(uploadedVoice);
      if ((music && !uploadedMusic?.assetId) || (voiceover && !uploadedVoice?.assetId)) {
        throw new Error('Seçilen ses dosyası yüklenemedi; sessiz video oluşturulmadı.');
      }
      const created = await responseJson(await fetch(`${API}/api/projects/${project.id}/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${project.token}` },
        body: JSON.stringify({
          clips: uploadedClips.map((clip) => clip.assetId), music: uploadedMusic?.assetId,
          voiceover: uploadedVoice?.assetId, options,
        }),
      }));
      localStorage.setItem(JOB_KEY, JSON.stringify({ id: created.id, projectId: project.id }));
      setJob(created);
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); setUploadProgress(null); }
  };

  const moveClip = (index, direction) => setClips((current) => {
    const target = index + direction;
    if (target < 0 || target >= current.length) return current;
    const next = [...current]; [next[index], next[target]] = [next[target], next[index]]; return next;
  });

  const removeClip = (index) => setClips((current) => {
    URL.revokeObjectURL(current[index].url);
    return current.filter((_, itemIndex) => itemIndex !== index);
  });

  const shareVideo = async () => {
    if (!outputUrl) return;
    const file = new File([await (await fetch(outputUrl)).blob()], 'clipflow-video.mp4', { type: 'video/mp4' });
    if (navigator.canShare?.({ files: [file] })) await navigator.share({ files: [file], title: 'ClipFlow Studio videosu' });
    else { const anchor = document.createElement('a'); anchor.href = outputUrl; anchor.download = file.name; anchor.click(); }
  };

  const resetProject = async () => {
    try {
      const next = await responseJson(await fetch(`${API}/api/projects`, { method: 'POST' }));
      localStorage.setItem(PROJECT_KEY, JSON.stringify(next));
      localStorage.removeItem(JOB_KEY);
      clips.forEach((clip) => releaseObjectUrl(clip.url));
      if (music?.url) releaseObjectUrl(music.url);
      if (voiceover?.url) releaseObjectUrl(voiceover.url);
      if (outputUrl) releaseObjectUrl(outputUrl);
      setProject(next); setClips([]); setMusic(null); setVoiceover(null); setJob(null); setOutputUrl(''); setError('');
    } catch (reason) { setError(reason.message); }
  };

  const patchOptions = (patch) => setOptions((current) => ({ ...current, ...patch }));
  const patchSubtitle = (patch) => setOptions((current) => ({ ...current, subtitle_style: { ...current.subtitle_style, ...patch } }));
  const activeStatus = job?.status === 'queued' ? 'Sırada'
    : job?.status === 'processing'
      ? ({
        resolving_voiceover: 'Seslendirme çözümleniyor',
        transcribing_subtitles: 'Konuşma çözümleniyor',
        preparing_subtitles: 'Altyazılar hazırlanıyor',
        rendering_video: 'Video oluşturuluyor',
      }[job.phase] || 'Hazırlanıyor')
      : job?.status === 'completed' ? 'Tamamlandı' : job?.status === 'error' ? 'Hata' : '';

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="ClipFlow Studio ana sayfa"><span className="brand-mark"><Clapperboard size={19} /></span><span>clipflow<span className="brand-light">studio</span></span></a>
        <div className="top-meta"><span className="live-dot" /> <span>YENİ PROJE</span><span className="top-divider" /> <span>9:16 · 1080P</span></div>
        <button className="icon-button top-help" title="Yeni proje" aria-label="Yeni proje" onClick={resetProject}><Sparkles size={17} /></button>
      </header>

      <div className="workspace" id="top">
        <section className="editing-column">
          <div className="page-heading"><div><p className="eyebrow">MOBİL VİDEO STÜDYOSU</p><h1>Yeni bir akış<br className="mobile-break" /> oluştur.</h1></div><div className="duration-pill"><span className="duration-label">TAHMİNİ SÜRE</span><strong>{formatTime(totalDuration)}</strong></div></div>

          <section className="section-block clips-section">
            <div className="section-heading"><div className="section-title"><span className="section-index">01</span><div><h2>Klip sırası</h2><p>{clips.length ? `${clips.length} video · sıralama dışa aktarmaya uygulanır` : 'Videolarını sırala ve düzenle'}</p></div></div><span className="count-badge">{String(clips.length).padStart(2, '0')}</span></div>
            <input ref={clipInput} className="visually-hidden" type="file" accept="video/*" multiple onChange={(event) => addFiles(event.target.files)} />
            {clips.length === 0 ? <button className="dropzone" onClick={() => clipInput.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files); }}>
              <span className="upload-glyph"><Upload size={21} /></span><span className="drop-title">Klipleri buraya bırak</span><span className="drop-subtitle">veya <b>dosya seç</b></span><span className="drop-foot">MP4 · MOV · WEBM</span>
            </button> : <div className="clip-list">
              {clips.map((clip, index) => <article key={clip.id} className={`clip-row ${dragIndex === index ? 'is-dragging' : ''}`} draggable onDragStart={() => setDragIndex(index)} onDragEnd={() => setDragIndex(null)} onDragOver={(event) => event.preventDefault()} onDrop={() => { if (dragIndex !== null && dragIndex !== index) setClips((current) => { const next = [...current]; const [item] = next.splice(dragIndex, 1); next.splice(index, 0, item); return next; }); setDragIndex(null); }}>
                <span className="drag-handle" aria-label="Sürükleyerek sırala"><GripVertical size={16} /></span><span className="clip-number">{String(index + 1).padStart(2, '0')}</span>
                <div className="clip-thumb"><video src={clip.url} muted playsInline preload="metadata" /><span className="thumb-play"><FileVideo2 size={14} /></span></div>
                <div className="clip-info"><strong title={clip.file.name}>{clip.file.name}</strong><span>{formatTime(clip.duration)} <i /> {(clip.file.size / 1024 / 1024).toFixed(1)} MB</span></div>
                <div className="clip-actions"><button className="tiny-button" title="Yukarı taşı" aria-label="Yukarı taşı" disabled={index === 0} onClick={() => moveClip(index, -1)}><ArrowUp size={15} /></button><button className="tiny-button" title="Aşağı taşı" aria-label="Aşağı taşı" disabled={index === clips.length - 1} onClick={() => moveClip(index, 1)}><ArrowDown size={15} /></button><button className="tiny-button remove-button" title="Klibi sil" aria-label="Klibi sil" onClick={() => removeClip(index)}><Trash2 size={15} /></button></div>
              </article>)}
              <button className="add-clip" onClick={() => clipInput.current?.click()}><Plus size={15} /> Klip ekle</button>
            </div>}
          </section>

          <section className="section-block audio-section">
            <div className="section-heading"><div className="section-title"><span className="section-index">02</span><div><h2>Ses katmanları</h2><p>Müzik ve seslendirmeyi ayrı ayrı ayarla</p></div></div></div>
            <div className="audio-grid">
              {[{ key: 'music', label: 'Arka plan müziği', icon: Music2, value: music, ref: musicInput, accent: 'lime' }, { key: 'voiceover', label: 'Seslendirme', icon: AudioLines, value: voiceover, ref: voiceInput, accent: 'coral' }].map((track) => <div className={`audio-track ${track.value ? 'has-file' : ''}`} key={track.key}>
                <input ref={track.ref} className="visually-hidden" type="file" accept=".mp3,.wav,.m4a,.aac,.ogg,audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/ogg" onChange={(event) => { void setAudio(event.target.files[0], track.key); event.target.value = ''; }} />
                <button className="audio-pick" onClick={() => track.ref.current?.click()}><span className={`audio-icon ${track.accent}`}><track.icon size={17} /></span><span className="audio-copy"><b>{track.label}</b><small title={track.value?.file.name}>{track.value ? track.value.file.name : 'MP3 · WAV · M4A · AAC'}</small></span>{track.value ? <span className="audio-duration">{formatTime(track.value.duration)}</span> : <Plus size={16} className="audio-plus" />}</button>
                {track.value && <button className="audio-remove" title="Ses dosyasını kaldır" aria-label={`${track.label} dosyasını kaldır`} onClick={() => { const setter = track.key === 'music' ? setMusic : setVoiceover; setter(null); URL.revokeObjectURL(track.value.url); }}><X size={14} /></button>}
                {track.value && <div className={`audio-upload-state ${track.value.uploadStatus}`} role="status">{track.value.uploadStatus === 'uploaded' ? 'Yüklendi' : track.value.uploadStatus === 'uploading' ? `Yükleniyor${track.value.uploadPercent ? ` · ${track.value.uploadPercent}%` : '…'}` : track.value.uploadStatus === 'failed' ? 'Yükleme başarısız · tekrar deneyin' : track.value.uploadStatus === 'reading' ? 'Ses bilgileri okunuyor…' : track.value.uploadStatus === 'invalid' ? 'Dosya okunamadı · yeniden seçin' : 'Render sırasında yüklenecek'}</div>}
                {track.value && <div className="track-controls"><label><span>Başlangıç <b>{Number(options[`${track.key}_start`]).toFixed(1)} sn</b></span><input type="range" min="0" max={Math.max(30, Math.ceil(totalDuration))} step="0.5" value={options[`${track.key}_start`]} onChange={(event) => patchOptions({ [`${track.key}_start`]: Number(event.target.value) })} /></label><label><span>Ses seviyesi <b>{Math.round(options[`${track.key}_volume`] * 100)}%</b></span><input type="range" min="0" max="1" step="0.01" value={options[`${track.key}_volume`]} onChange={(event) => patchOptions({ [`${track.key}_volume`]: Number(event.target.value) })} /></label></div>}
                {track.value && <label className="audio-loop-control"><input type="checkbox" checked={options[`${track.key}_loop`]} onChange={(event) => patchOptions({ [`${track.key}_loop`]: event.target.checked })} /><span>Kısa ses dosyasını videonun sonuna kadar tekrarla</span></label>}
              </div>)}
            </div>
            <label className="toggle-line"><span className="toggle-icon"><Volume2 size={16} /></span><span><b>Klip sesini koru</b><small>Kaynak videolardaki konuşmayı açık tut</small></span><input type="checkbox" checked={options.keep_clip_audio} onChange={(event) => patchOptions({ keep_clip_audio: event.target.checked })} /><i className="toggle-ui" /></label>
          </section>

          <section className="section-block captions-section">
            <div className="section-heading"><div className="section-title"><span className="section-index">03</span><div><h2>Altyazı</h2><p>Konuşma tanıma ile senkron altyazı</p></div></div><span className="ai-tag"><Sparkles size={12} /> WHISPER</span></div>
            <div className="caption-source"><label htmlFor="subtitle-source">Altyazı kaynağı</label><div className="select-wrap"><select id="subtitle-source" value={options.subtitle_source} onChange={(event) => patchOptions({ subtitle_source: event.target.value })}><option value="none">Altyazısız</option><option value="voiceover">Eklediğim ses dosyasından oluştur (ElevenLabs)</option><option value="clips">Kliplerdeki orijinal konuşmadan oluştur</option></select><ChevronDown size={15} /></div></div>
            {options.subtitle_source !== 'none' && <><div className="caption-controls"><label className="control-field"><span>Dil</span><div className="select-wrap"><select value={options.subtitle_language} onChange={(event) => patchOptions({ subtitle_language: event.target.value })}>{Object.entries(languageNames).map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select><ChevronDown size={15} /></div></label><label className="control-field"><span>Yazı tipi</span><select value={options.subtitle_style.font_name} onChange={(event) => patchSubtitle({ font_name: event.target.value })}><option value="Liberation Sans">Liberation Sans</option><option value="DejaVu Sans">DejaVu Sans</option></select></label><label className="control-field"><span>Yazı boyutu</span><input type="number" min="16" max="96" value={options.subtitle_style.font_size} onChange={(event) => patchSubtitle({ font_size: Number(event.target.value) })} /></label><label className="control-field"><span>Renk</span><select value={options.subtitle_style.color} onChange={(event) => patchSubtitle({ color: event.target.value })}><option value="white">Beyaz</option><option value="yellow">Sarı</option></select></label><label className="control-field"><span>Konum</span><select value={options.subtitle_style.position} onChange={(event) => patchSubtitle({ position: Number(event.target.value) })}><option value="2">Alt</option><option value="5">Orta</option><option value="8">Üst</option></select></label></div><label className="caption-background"><span>Altyazı arka planı</span><input type="checkbox" checked={options.subtitle_style.background} onChange={(event) => patchSubtitle({ background: event.target.checked })} /><i className="toggle-ui" /></label></>}
            <p className="caption-note"><FileAudio2 size={14} /> Seslendirme altyazısı için Seslendirme alanına dosya ekleyin; müzik transkripsiyon kaynağı değildir.</p>
          </section>

          <section className="section-block frame-section">
            <div className="section-heading"><div className="section-title"><span className="section-index">04</span><div><h2>Görüntü</h2><p>Dikey format · 1080 × 1920</p></div></div></div>
            <div className="segmented-control" role="group" aria-label="Görüntü sığdırma modu"><button className={options.fit_mode === 'crop' ? 'selected' : ''} onClick={() => patchOptions({ fit_mode: 'crop' })}><span className="frame-icon crop-icon" /> Kırp ve doldur</button><button className={options.fit_mode === 'fit' ? 'selected' : ''} onClick={() => patchOptions({ fit_mode: 'fit' })}><span className="frame-icon fit-icon" /> Sığdır ve kenarlık</button></div>
          </section>

          <section className="render-panel">
            <div className="render-copy"><span className="render-kicker"><span className="live-dot" /> DIŞA AKTAR</span><h2>{job?.status === 'completed' ? 'Videon hazır.' : 'Akışını oluştur.'}</h2><p>MP4 · 1080 × 1920 · 9:16</p></div>
            <button className="render-button" disabled={busy || ['queued', 'processing'].includes(job?.status)} onClick={renderVideo}>{busy ? <><LoaderCircle className="spin" size={18} /> Yükleniyor</> : ['queued', 'processing'].includes(job?.status) ? <><LoaderCircle className="spin" size={18} /> Oluşturuluyor</> : <><Clapperboard size={18} /> Videoyu oluştur</>}</button>
            {uploadProgress && <div className="upload-progress"><div><span>Dosyalar yükleniyor</span><b>{uploadProgress.done}/{uploadProgress.total}{uploadProgress.percent ? ` · ${uploadProgress.percent}%` : ''}</b></div><progress max="100" value={uploadProgress.percent || uploadProgress.done / uploadProgress.total * 100} /></div>}
            {job && <div className={`job-status ${job.status}`}><span className="status-icon">{job.status === 'completed' ? <Check size={16} /> : job.status === 'error' ? <X size={16} /> : <LoaderCircle className="spin" size={16} />}</span><div><b>{activeStatus}</b>{job.status === 'processing' && <span> · %{job.progress || 0}</span>}{job.error && <p>{job.error}</p>}</div></div>}
            {error && <div className="error-message"><span>{error}</span><button title="Mesajı kapat" onClick={() => setError('')}><X size={15} /></button></div>}
          </section>

          {outputUrl && <section className="result-panel"><div className="result-head"><span className="result-mark"><Check size={16} /></span><div><b>Render tamamlandı</b><small>MP4 dosyan paylaşmaya hazır</small></div><span className="result-format">MP4</span></div><video className="result-video" src={outputUrl} controls playsInline /><div className="result-actions"><a className="download-button" href={outputUrl} download="clipflow-video.mp4"><Download size={16} /> Videoyu indir</a><button className="share-button" onClick={shareVideo}><Share2 size={16} /> Paylaş</button></div><p className="ios-note"><b>iPhone:</b> İndirilen videoyu Fotoğraflar'a eklemek için Paylaş menüsünü açıp <b>Videoyu Kaydet</b>'e dokun. Otomatik kaydetme Safari tarafından garanti edilmez.</p></section>}
        </section>

        <aside className="preview-column">
          <div className="preview-sticky"><div className="preview-topline"><span>ÖNİZLEME</span><span className="preview-ratio">9:16</span></div><div className="phone-frame"><div className="phone-screen">{clips.length ? <video key={clips[0].url} src={clips[0].url} muted playsInline controls /> : <div className="preview-empty"><span className="preview-symbol"><Clapperboard size={25} /></span><span>Önizleme alanı</span><small>İlk klibini eklediğinde burada görünür</small></div>}<span className="phone-safe-area" /></div></div><div className="preview-caption"><span className="preview-caption-dot" /><span>ÇIKTI ÖNİZLEMESİ</span><span className="preview-caption-right">1080 × 1920</span></div><div className="render-specs"><div><span>Dosya</span><b>MP4 / H.264</b></div><div><span>Kare hızı</span><b>30 FPS</b></div><div><span>Ses</span><b>AAC · 48 kHz</b></div></div><div className="project-chip"><span className={`project-state ${project ? 'ready' : ''}`} /><span>{project ? 'Proje kaydedildi' : 'Proje hazırlanıyor'}</span><span className="project-save"><Check size={12} /> İş takibi açık</span></div></div>
        </aside>
      </div>
      <footer className="footer"><span>CLIPFLOW STUDIO <i /> 2026</span><span>Her klip bir hikâye.</span></footer>
    </main>
  );
}
