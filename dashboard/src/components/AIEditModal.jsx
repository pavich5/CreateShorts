import React, { useId, useRef, useState } from 'react';
import { ArrowRight, Check, Clock3, Crosshair, Film, Loader2, LockKeyhole, Play, Scissors, Sparkles, Sun, VolumeX, Wand2 } from 'lucide-react';
import Modal from './ui/Modal';
import ClipPreview from './ClipPreview';
import { apiJson, openQuotaWall, QuotaError } from '../lib/api';
import { aiEditDraft, editPlaybackWindow, formatTimestamp, scopedInstructions, validEditRange, validTrimRange } from '../lib/aiEditControls';
import { useEditorDraft } from '../lib/useEditorDraft';
import { getApiUrl } from '../config';
import './ai-edit-modal.css';

const SUGGESTIONS = [
  { label: 'Start here', icon: Scissors, wholeClip: true, prompt: time => `Start the video at ${time.toFixed(1)} seconds. Remove everything before this point and keep the rest.` },
  { label: 'Find source footage', icon: Film, prompt: 'Find the reveal or result in the full original video and add it at the end, including the reaction and original sound.' },
  { label: 'Steady framing', icon: Crosshair, prompt: 'Keep the main speaker steadily framed. Stop switching to inset faces and smooth the framing through source layout changes.' },
  { label: 'Reduce shake', icon: Wand2, prompt: 'Reduce camera shake for smoother footage.' },
  { label: 'Adjust lighting', icon: Sun, prompt: 'Make the picture a little brighter while keeping it natural.' },
  { label: 'Mute audio', icon: VolumeX, prompt: 'Mute the audio.' },
];

export default function AIEditModal({ onClose, videoUrl, jobId, clipIndex = 0, inputFilename, fileId, onApplied, onProcessingChange, onEditorStateChange, embedded = false, initialMode = 'ai' }) {
  const formId = useId();
  const promptId = useId();
  const [instructions, setInstructions] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [changes, setChanges] = useState([]);
  const [originalUrl] = useState(videoUrl);
  const [renderedUrl, setRenderedUrl] = useState(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const videoRef = useRef(null);
  const textareaRef = useRef(null);
  const restoreTime = useRef(0);
  const playOnLoad = useRef(false);
  const previewStopAt = useRef(null);
  const submitting = useRef(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [previewError, setPreviewError] = useState(false);
  const [rangeMode, setRangeMode] = useState(false);
  const [rangeStart, setRangeStart] = useState(0);
  const [rangeEnd, setRangeEnd] = useState(0);
  const [editMode, setEditMode] = useState(initialMode === 'trim' ? 'trim' : 'ai');
  const [trimMode, setTrimMode] = useState('keep');
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const isTrimming = editMode === 'trim';
  const hasEdits = changes.length > 0;
  const editingDisabled = busy || showOriginal || !duration;
  const rangeValid = !rangeMode || validEditRange(rangeStart, rangeEnd, duration);
  const trimValid = validEditRange(trimStart, trimEnd, duration);
  const trimCanApply = trimMode === 'remove' ? trimValid && duration - (trimEnd - trimStart) >= .15 : validTrimRange(trimStart, trimEnd, duration);
  const canApply = !editingDisabled && (isTrimming ? trimCanApply : !!instructions.trim() && instructions.length <= 1700 && rangeValid);
  const draftValues = { instructions, rangeMode, rangeStart, rangeEnd, trimStart, trimEnd, trimMode };
  const { dirty: draftDirty, markSaved } = useEditorDraft({
    value: aiEditDraft(editMode, draftValues),
    ready: Boolean(duration) && (!isTrimming || trimEnd > 0), canSave: canApply,
    save: () => apply(), onStateChange: onEditorStateChange,
  });
  const previewUrl = showOriginal ? originalUrl : (renderedUrl || videoUrl);

  const seek = value => {
    const time = Math.max(0, Math.min(duration || 0, value));
    if (videoRef.current) videoRef.current.currentTime = time;
    setCurrentTime(time);
  };
  const readTiming = event => {
    const video = event.currentTarget;
    setCurrentTime(Number.isFinite(video.currentTime) ? video.currentTime : 0);
    if (Number.isFinite(video.duration) && video.duration > 0) setDuration(video.duration);
    if (previewStopAt.current !== null && video.currentTime >= previewStopAt.current) {
      const editedFrame = previewStopAt.current;
      previewStopAt.current = null;
      video.pause();
      video.currentTime = editedFrame;
      setCurrentTime(editedFrame);
    }
  };
  const changeVersion = original => {
    if (original === showOriginal) return;
    restoreTime.current = currentTime;
    playOnLoad.current = false;
    previewStopAt.current = null;
    setDuration(null);
    setShowOriginal(original);
    setPlaying(false);
  };
  const insertTimestamp = () => {
    const input = textareaRef.current;
    const start = input?.selectionStart ?? instructions.length;
    const end = input?.selectionEnd ?? start;
    const text = `${currentTime.toFixed(1)} seconds`;
    setInstructions(instructions.slice(0, start) + text + instructions.slice(end));
    input?.focus();
    requestAnimationFrame(() => input?.setSelectionRange(start + text.length, start + text.length));
  };
  const previewTrim = () => {
    if (!trimValid || editingDisabled || !videoRef.current) return;
    const video = videoRef.current;
    previewStopAt.current = trimEnd;
    video.currentTime = trimStart;
    setCurrentTime(trimStart);
    video.play().catch(() => {});
  };
  const apply = async event => {
    event?.preventDefault?.();
    if (!canApply || (isTrimming && !draftDirty) || submitting.current) return false;
    submitting.current = true;
    videoRef.current?.pause();
    setBusy(true); setError('');
    onProcessingChange?.(true);
    try {
      const target = { job_id: jobId, clip_index: clipIndex, file_id: fileId || null, input_filename: inputFilename };
      const endpoint = isTrimming ? '/api/edit/trim' : '/api/edit/ai';
      const result = await apiJson(endpoint, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(isTrimming ? { ...target, start: trimStart, end: trimEnd, mode: trimMode } : { ...target,
          playhead_time: Number.isFinite(currentTime) ? currentTime : null,
          instructions: scopedInstructions(instructions, rangeMode ? { start: rangeStart, end: rangeEnd } : null),
          range_start: rangeMode ? rangeStart : null, range_end: rangeMode ? rangeEnd : null }),
      });
      if (!result.success || !result.new_video_url) throw new Error('The server did not return an edited video.');
      const start = Number.isFinite(result.preview_start) ? result.preview_start : (rangeMode ? rangeStart : 0);
      const end = Number.isFinite(result.preview_end) ? result.preview_end : (rangeMode ? rangeEnd : duration);
      // Seek INSIDE the changed section: pausing on its exact start can show
      // the preceding, unedited frame after a fractional-second edit.
      const window = editPlaybackWindow(start, end);
      restoreTime.current = window?.start ?? start;
      previewStopAt.current = window?.end ?? null;
      playOnLoad.current = true;
      setShowOriginal(false);
      setRenderedUrl(getApiUrl(result.new_video_url));
      if (Number.isFinite(result.duration_seconds)) {
        setDuration(result.duration_seconds);
        setRangeMode(false);
        setRangeStart(0);
        setRangeEnd(result.duration_seconds);
        setTrimMode('keep');
        setTrimStart(0);
        setTrimEnd(result.duration_seconds);
      }
      const savedValues = { ...draftValues, instructions: '' };
      if (Number.isFinite(result.duration_seconds)) Object.assign(savedValues, {
        rangeMode: false, rangeStart: 0, rangeEnd: result.duration_seconds,
        trimStart: 0, trimEnd: result.duration_seconds, trimMode: 'keep',
      });
      markSaved(aiEditDraft(editMode, savedValues));
      onApplied(result);
      setChanges(result.changes || []);
      setInstructions('');
      return true;
    } catch (err) {
      if (err instanceof QuotaError) openQuotaWall(err, 'ai-edit');
      setError(err.detail || err.message || 'Could not apply this edit. Your current clip is unchanged. Please try again.');
      return false;
    } finally {
      submitting.current = false;
      setBusy(false);
      onProcessingChange?.(false);
    }
  };

  return <Modal isOpen embedded={embedded} onClose={busy ? undefined : onClose} title={isTrimming ? 'Trim clip' : 'Edit with AI'} eyebrow={isTrimming ? 'YOUR CLIP. YOUR TIMING.' : 'YOUR WORDS. YOUR EDIT.'} size="xl" className="ai-editor-modal" bodyClassName="ai-editor-modal-body" footer={<div className="ai-editor-footer">
    {showOriginal ? <p role="status"><LockKeyhole size={14}/><span>Switch to Edited to make another change.</span></p> : error ? <p role="alert" className="ai-footer-error">{error}</p> : <p role="status">{busy ? <Loader2 size={14} className="animate-spin"/> : hasEdits ? <Check size={14}/> : <LockKeyhole size={14}/>}<span>{busy ? 'Rendering changes into your video…' : hasEdits ? 'Edit saved to this clip. The preview shows the updated video.' : 'Your original video is kept'}</span></p>}
    <div><button type="button" onClick={onClose} disabled={busy} className="btn-quiet">{embedded || hasEdits ? 'Done' : 'Close'}</button>
      <button type="button" onClick={apply} disabled={!canApply || (isTrimming && !draftDirty)} className="btn-primary ai-apply-button">{busy ? <Loader2 size={16} className="animate-spin"/> : isTrimming ? <Scissors size={16}/> : <Sparkles size={16}/>} {busy ? 'Applying changes…' : isTrimming ? 'Apply trim' : 'Apply changes'}{!busy && <ArrowRight size={16}/>}</button></div>
  </div>}>
    <div className="ai-editor-layout">
      <ClipPreview className="ai-preview-panel" controls={{ time: currentTime, duration, playing, muted, disabled: busy || previewError,
        toggle: () => { const v = videoRef.current; if (v?.paused) v.play().catch(() => {}); else v?.pause(); }, seek, toggleMute: () => setMuted(value => !value) }}
        selection={isTrimming || rangeMode ? { start: isTrimming ? trimStart : rangeStart, end: isTrimming ? trimEnd : rangeEnd } : null}
        hint={isTrimming ? (trimMode === 'remove' ? 'Purple marks the section you’ll remove.' : 'Purple marks the part you’ll keep.') : rangeMode ? 'Purple marks the range you’re editing.' : 'Scrub to any moment in your clip.'}
        actions={hasEdits ? <div className="ai-version-switch" role="group" aria-label="Preview version">
          <button type="button" aria-pressed={showOriginal} onClick={() => changeVersion(true)} disabled={busy}>Original</button>
          <button type="button" aria-pressed={!showOriginal} onClick={() => changeVersion(false)} disabled={busy}>Edited</button>
        </div> : null}>
          <video ref={videoRef} key={previewUrl} src={previewUrl} playsInline muted={muted} preload="metadata" aria-label="AI edit preview"
            onLoadStart={() => { setCurrentTime(0); setPreviewError(false); setPlaying(false); }}
            onLoadedMetadata={event => {
              readTiming(event);
              const video = event.currentTarget;
              if (Number.isFinite(video.duration) && video.duration > 0) {
                if (isTrimming && playOnLoad.current && !showOriginal) {
                  // The encoded video ends on a frame boundary. Use its real
                  // duration for both the controls and the saved baseline.
                  setTrimStart(0); setTrimEnd(video.duration); setTrimMode('keep');
                  setRangeStart(0); setRangeEnd(video.duration); setRangeMode(false);
                  markSaved(aiEditDraft(editMode, { ...draftValues, trimStart: 0, trimEnd: video.duration,
                    trimMode: 'keep', rangeMode: false, rangeStart: 0, rangeEnd: video.duration }));
                } else setTrimEnd(previous => previous > 0 ? previous : video.duration);
              }
              if (Number.isFinite(video.duration)) video.currentTime = Math.min(restoreTime.current, video.duration);
              restoreTime.current = 0;
            }} onDurationChange={readTiming} onTimeUpdate={readTiming} onSeeking={readTiming} onSeeked={event => {
              readTiming(event);
              if (playOnLoad.current) {
                playOnLoad.current = false;
                const video = event.currentTarget;
                video.play().catch(() => {
                  // Browsers may block autoplay with audio after a long render.
                  // Keep the edited section visible and allow muted playback.
                  video.muted = true;
                  setMuted(true);
                  video.play().catch(() => {});
                });
              }
            }}
            onPlay={event => {
              setPlaying(true);
              const current = event.currentTarget;
              current.ownerDocument.querySelectorAll('video').forEach(video => { if (video !== current) video.pause(); });
            }} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setPreviewError(true)}/>
          {!playing && !previewError && <button type="button" className="clip-preview-play" aria-label="Play preview" disabled={!duration || busy} onClick={() => videoRef.current?.play().catch(() => {})}><Play size={25} fill="currentColor"/></button>}
          {previewError && <div className="clip-preview-message"><Film size={28}/><p>Preview unavailable</p><button type="button" onClick={() => {setPreviewError(false);videoRef.current?.load();}}>Retry preview</button></div>}
          {busy && <div className="clip-preview-message"><Loader2 size={28} className="animate-spin"/><p>Bringing your edit to life</p><span>This can take a moment.</span></div>}
      </ClipPreview>

      <form id={formId} onSubmit={apply} className="ai-instruction-panel" onKeyDown={event => {if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') apply(event);}}>
        {!embedded && <div className="ai-mode-switch" role="group" aria-label="Editing mode">
          <button type="button" aria-pressed={editMode === 'ai'} disabled={busy} onClick={() => {setEditMode('ai');setError('');previewStopAt.current = null;}}><Sparkles size={15}/>AI edit</button>
          <button type="button" aria-pressed={isTrimming} disabled={busy} onClick={() => {setEditMode('trim');setError('');previewStopAt.current = null;}}><Scissors size={15}/>Trim clip</button>
        </div>}
        {isTrimming ? <>
          <div className="ai-editor-intro"><span className="ai-section-kicker">KEEP THE BEST PART</span><h3>Keep the best parts of your clip.</h3><p>Keep a range, or remove a section from anywhere in the video.</p></div>
          <fieldset className="ai-scope-section" disabled={editingDisabled}><legend>{trimMode === 'remove' ? 'Section to remove' : 'Part to keep'}</legend>
            <div className="ai-scope-toggle" role="group" aria-label="Trim action"><button type="button" aria-pressed={trimMode === 'keep'} onClick={() => {setTrimMode('keep');setTrimStart(0);setTrimEnd(duration);}}>Keep a range</button><button type="button" aria-pressed={trimMode === 'remove'} onClick={() => {setTrimMode('remove');const start=Math.min(currentTime,Math.max(0,duration-1));setTrimStart(Number(start.toFixed(3)));setTrimEnd(Math.min(duration,start+1));}}>Remove a section</button></div>
            <div className="ai-range-editor ai-trim-editor">
              <div className="ai-range-fields"><label>Start <span>seconds</span><input aria-label="Trim start in seconds" type="number" min="0" max={duration || 0} step="0.01" value={trimStart} onChange={event => {setTrimStart(event.target.value === '' ? '' : Number(event.target.value));setError('');}}/><button type="button" onClick={() => {setTrimStart(Number(currentTime.toFixed(3)));setError('');}}>Set at playhead</button></label>
                <span className="ai-range-arrow"><ArrowRight size={16}/></span>
                <label>End <span>seconds</span><input aria-label="Trim end in seconds" type="number" min="0" max={duration || 0} step="0.01" value={trimEnd} onChange={event => {setTrimEnd(event.target.value === '' ? '' : Number(event.target.value));setError('');}}/><button type="button" onClick={() => {setTrimEnd(Math.min(duration, Number(currentTime.toFixed(3))));setError('');}}>Set at playhead</button></label></div>
              <div className="ai-trim-sliders"><label>Start<input aria-label="Drag trim start" type="range" min="0" max={Math.max(0, (duration || 0) - .15)} step="0.01" value={Number(trimStart) || 0} onChange={event => {const start = Number(event.target.value);setTrimStart(start);if (!Number.isFinite(trimEnd) || trimEnd - start < .15) setTrimEnd(Math.min(duration, start + .15));setError('');}}/></label>
                <label>End<input aria-label="Drag trim end" type="range" min={Math.min(.15, duration || 0)} max={duration || 0} step="0.01" value={Number(trimEnd) || 0} onChange={event => {const end = Number(event.target.value);setTrimEnd(end);if (!Number.isFinite(trimStart) || end - trimStart < .15) setTrimStart(Math.max(0, end - .15));setError('');}}/></label></div>
              {!(trimMode === 'remove' ? trimCanApply : trimValid) ? <p role="alert" className="ai-range-error">Choose a valid section and leave at least 0.15 seconds in the video.</p> : <p className="ai-range-summary"><Scissors size={13}/>{formatTimestamp(trimStart)} – {formatTimestamp(trimEnd)}<span>{(trimEnd - trimStart).toFixed(2)}s {trimMode === 'remove' ? 'removed' : 'kept'}</span></p>}
            </div>
            <div className="ai-trim-actions"><button type="button" className="btn-quiet" disabled={!trimValid || previewError} onClick={previewTrim}><Play size={14}/>Preview section</button><button type="button" onClick={() => {setTrimMode('keep');setTrimStart(0);setTrimEnd(duration);setError('');previewStopAt.current = null;}}>Reset</button></div>
          </fieldset>
          <p className="ai-capabilities">Video, sound, captions and overlays stay together. The remaining parts join together when you apply the trim.</p>
        </> : <>
        <div className="ai-editor-intro"><span className="ai-section-kicker">MAKE IT YOURS</span><h3>A better edit starts with a few words.</h3><p>Describe a change, or ask AI to find a moment in the original video.</p></div>
        <fieldset className="ai-scope-section" disabled={editingDisabled}><legend>Where should it change?</legend>
          <div className="ai-scope-toggle"><button type="button" aria-pressed={!rangeMode} onClick={() => setRangeMode(false)}>Whole clip</button>
            <button type="button" aria-pressed={rangeMode} disabled={!duration} onClick={() => { if (!rangeMode && !validEditRange(rangeStart, rangeEnd, duration)) {setRangeStart(Math.floor(Math.min(currentTime, Math.max(0, duration - .2)) * 1000) / 1000);setRangeEnd(Math.floor(Math.min(duration, Math.max(currentTime + 2, .2)) * 1000) / 1000);} setRangeMode(true);}}>Time range</button></div>
          {rangeMode && <div className="ai-range-editor">
            <div className="ai-range-fields"><label>Start <span>seconds</span><input aria-label="Edit range start in seconds" type="number" min="0" max={duration || 0} step="any" value={rangeStart} onChange={event => setRangeStart(event.target.value === '' ? '' : Number(event.target.value))}/><button type="button" onClick={() => setRangeStart(Number(currentTime.toFixed(1)))}>Set at playhead</button></label>
              <span className="ai-range-arrow"><ArrowRight size={16}/></span>
              <label>End <span>seconds</span><input aria-label="Edit range end in seconds" type="number" min="0" max={duration || 0} step="any" value={rangeEnd} onChange={event => setRangeEnd(event.target.value === '' ? '' : Number(event.target.value))}/><button type="button" onClick={() => setRangeEnd(Math.min(duration, Number(currentTime.toFixed(1))))}>Set at playhead</button></label></div>
            {!rangeValid ? <p role="alert" className="ai-range-error">Choose an end after the start, within this clip.</p> : <p className="ai-range-summary"><Crosshair size={13}/>{formatTimestamp(rangeStart)} – {formatTimestamp(rangeEnd)}<span>Only this range will change</span></p>}
          </div>}
        </fieldset>
        <div className="ai-prompt-section"><label htmlFor={promptId}>What would you like to change?</label>
          <div className="ai-prompt-box"><textarea ref={textareaRef} id={promptId} data-autofocus required maxLength={1700} rows={4} value={instructions} onChange={event => {setInstructions(event.target.value);setError('');}} disabled={editingDisabled} placeholder={rangeMode ? 'Show the original wide shot here so we can see the car and his reaction…' : 'Find the car reveal in the original video and add it at the end, including his reaction…'}/>
            <div><button type="button" onClick={insertTimestamp} disabled={editingDisabled}><Clock3 size={13}/>Insert {formatTimestamp(currentTime)}</button><span>{instructions.length}/1700</span></div></div>
        </div>
        <div className="ai-suggestions"><p>Try a quick edit</p><div>{SUGGESTIONS.map(({ label, icon, prompt, wholeClip }) => <button key={label} type="button" disabled={editingDisabled || (wholeClip && (currentTime < .05 || duration - currentTime < .15))} onClick={() => {setInstructions(typeof prompt === 'function' ? prompt(currentTime) : prompt);if (wholeClip) setRangeMode(false);setError('');textareaRef.current?.focus();}}>{React.createElement(icon, { size: 16 })}{label}<span><ArrowRight size={13}/></span></button>)}</div></div>
        <p className="ai-capabilities">Can trim this short and find or add footage from the full original video when it is available. Times refer to this short; searching can use the whole source. Also supports color, black & white, and subtle zooms.</p>
        </>}
        {busy && <p role="status" className="ai-edit-status"><Loader2 size={16} className="animate-spin"/>{isTrimming ? 'Trimming your clip…' : 'Interpreting your request and rendering the changes…'}</p>}
        {hasEdits && <div className="ai-edit-history" role="status"><h4><Check size={15}/>Changes applied</h4><p>Compare Original and Edited in the preview.</p>{changes.map((change, i) => <span key={i}>{change}</span>)}</div>}
      </form>
    </div>
  </Modal>;
}
