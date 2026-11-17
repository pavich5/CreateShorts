import { useEffect, useRef, useState } from 'react';
import { AlertCircle, Clock3, ImagePlus, Loader2, Move, Music2, Play, Trash2, Upload, Video } from 'lucide-react';
import Modal from './ui/Modal';
import ClipPreview from './ClipPreview';
import { apiFetch } from '../lib/api';
import { getApiUrl } from '../config';
import SyncedMedia from './SyncedMedia';
import VideoSourceRange from './VideoSourceRange';
import { useEditorDraft } from '../lib/useEditorDraft';
import CaptionPreviewLayer from './CaptionPreviewLayer';
import { savedCaptionPreview, savedHookPreview } from '../lib/editorPreviewLayers';
import { entranceStyle, mediaDuration, mediaKind, mediaLabel, mediaOffset, mediaSourceRange, mediaTimelineChange, mediaUrl, mediaVolume, placementError, replayEffectsPreview } from '../lib/mediaEditControls';
import './broll-modal.css';

const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const IMAGE_TYPES = /\.(?:jpe?g|png|webp|gif|bmp|tiff?)$/i;
const IMAGE_MIMES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp', 'image/tiff']);
const VIDEO_TYPES = /\.(?:mp4|mov|webm|mkv|m4v)$/i;
const AUDIO_TYPES = /\.(?:mp3|wav|m4a|aac|ogg|flac|opus)$/i;
const VISUAL_ACCEPT = '.jpg,.jpeg,.png,.webp,.gif,.bmp,.tif,.tiff,.mp4,.mov,.webm,.mkv,.m4v,image/*,video/*';
const AUDIO_ACCEPT = '.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus,audio/*';
const secondsLabel = value => `${Math.floor(value / 60)}:${(value % 60).toFixed(2).padStart(5, '0')}`;
const numericTime = value => value === '' ? NaN : Number(value);
const placementId = () => globalThis.crypto?.randomUUID?.() || `image-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const DEFAULT_APPEARANCE = { x: 0, y: 0, width: 100, height: 100, fit: 'stretch', opacity: 1, background: 'none' };
const RESIZE_HANDLES = [
    ['nw', 'top left'], ['n', 'top'], ['ne', 'top right'], ['e', 'right'],
    ['se', 'bottom right'], ['s', 'bottom'], ['sw', 'bottom left'], ['w', 'left'],
];
const clamp = (value, min, max) => Math.min(max, Math.max(min, Number(value) || 0));
const rounded = value => Number(value.toFixed(2));
const appearance = item => ({ ...DEFAULT_APPEARANCE, ...Object.fromEntries(Object.keys(DEFAULT_APPEARANCE).filter(key => item[key] != null).map(key => [key, item[key]])) });

function imageDimensions(url) {
    return new Promise(resolve => {
        const image = new Image();
        const timeout = setTimeout(() => { image.src = ''; resolve(null); }, 5000);
        image.onload = () => { clearTimeout(timeout); resolve({ width: image.naturalWidth, height: image.naturalHeight }); };
        image.onerror = () => { clearTimeout(timeout); resolve(null); };
        image.src = getApiUrl(url);
    });
}

function imageBounds(box, dimensions, videoRatio) {
    if (!dimensions?.width || !dimensions?.height) return box;
    const ratio = dimensions.width / dimensions.height;
    const width = Math.min(box.width, box.height * ratio / videoRatio);
    const height = Math.min(box.height, box.width * videoRatio / ratio);
    const x = rounded(box.x + (box.width - width) / 2);
    const y = rounded(box.y + (box.height - height) / 2);
    return { ...box, x, y, width: rounded(Math.max(.1, Math.min(width, 100 - x))), height: rounded(Math.max(.1, Math.min(height, 100 - y))) };
}

function resizeBounds(box, handle, dx, dy) {
    let left = box.x, top = box.y, right = box.x + box.width, bottom = box.y + box.height;
    if (handle.includes('w')) left = clamp(left + dx, 0, right - .1);
    if (handle.includes('e')) right = clamp(right + dx, left + .1, 100);
    if (handle.includes('n')) top = clamp(top + dy, 0, bottom - .1);
    if (handle.includes('s')) bottom = clamp(bottom + dy, top + .1, 100);
    left = rounded(left); top = rounded(top); right = rounded(right); bottom = rounded(bottom);
    return { x: left, y: top, width: rounded(right - left), height: rounded(bottom - top) };
}

async function responseJson(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The change could not be saved. Please try again.');
    return data;
}

export default function BrollModal({ jobId, clipIndex, videoUrl, durationInSeconds, onClose, onApplied, onProcessingChange, onDirtyChange, onEditorStateChange, embedded = false, audioOnly = false }) {
    const player = useRef(null);
    const uploadInput = useRef(null);
    const soundInput = useRef(null);
    const drag = useRef(null);
    const [loading, setLoading] = useState(true);
    const [ready, setReady] = useState(false);
    const [loadAttempt, setLoadAttempt] = useState(0);
    const [uploading, setUploading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [placements, setPlacements] = useState([]);
    const [duration, setDuration] = useState(Number(durationInSeconds) || 0);
    const [previewUrl, setPreviewUrl] = useState(videoUrl || '');
    const [aspectRatio, setAspectRatio] = useState(null);
    const [playhead, setPlayhead] = useState(0);
    const [playing, setPlaying] = useState(false);
    const [muted, setMuted] = useState(false);
    const [selectedId, setSelectedId] = useState(null);
    const [dirty, setDirty] = useState(false);
    const [revision, setRevision] = useState(null);
    const [postEffects, setPostEffects] = useState([]);
    const [captionsAbove, setCaptionsAbove] = useState(true);
    const [captionConfig, setCaptionConfig] = useState(null);
    const [hookConfig, setHookConfig] = useState(null);
    const [captionFreeUrl, setCaptionFreeUrl] = useState('');
    const endpoint = `/api/clip/${encodeURIComponent(jobId)}/${clipIndex}/broll`;
    const busy = uploading || saving;

    useEffect(() => {
        const controller = new AbortController();
        const filename = (videoUrl || '').split(/[?#]/)[0].split('/').pop();
        const query = '?live_preview=true' + (jobId.startsWith('file:') && filename?.startsWith('ai_') ? `&input_filename=${encodeURIComponent(filename)}` : '');
        const timeout = setTimeout(() => {
            controller.abort();
            setError('Loading media took too long. Try loading again.');
            setLoading(false);
        }, 15000);
        apiFetch(endpoint + query, { signal: controller.signal })
            .then(responseJson)
            .then(async data => {
                if (controller.signal.aborted) return;
                const videoRatio = data.width > 0 && data.height > 0 ? data.width / data.height : 9 / 16;
                const saved = await Promise.all((data.placements || []).map(async (item, index) => {
                    // Audio edits retain the saved geometry of every visual layer.
                    if (audioOnly) return { ...item, layer: item.layer ?? index, kind: mediaKind(item), id: item.id || placementId() };
                    // Old fitted images included empty/blurred space in their
                    // box. Put the handles on the picture itself instead.
                    const box = appearance(item);
                    const bounds = mediaKind(item) === 'image' && box.fit === 'contain' ? imageBounds(box, await imageDimensions(mediaUrl(item)), videoRatio) : box;
                    return { ...item, layer: item.layer ?? index, ...bounds, kind: mediaKind(item), fit: 'stretch', background: 'none', id: item.id || placementId() };
                }));
                if (controller.signal.aborted) return;
                setPlacements(saved);
                setDirty(!audioOnly && (data.placements || []).some(item => mediaKind(item) !== 'audio' && (item.fit !== 'stretch' || item.background !== 'none')));
                setSelectedId((audioOnly ? saved.find(item => mediaKind(item) === 'audio') : saved[0])?.id || null);
                setRevision(data.revision ?? null);
                setPostEffects(data.post_effects || []);
                setCaptionsAbove(data.captions_above_media !== false);
                setHookConfig(savedHookPreview(data.preview_hook, data.duration));
                setCaptionConfig(savedCaptionPreview(data.captions || [], data.caption_style, data.clip?.layout_ranges || []));
                setCaptionFreeUrl(data.caption_free_video_url ? getApiUrl(data.caption_free_video_url) : '');
                if (Number.isFinite(data.duration) && data.duration > 0) setDuration(data.duration);
                if (Number.isFinite(data.width) && Number.isFinite(data.height) && data.width > 0 && data.height > 0) setAspectRatio(data.width / data.height);
                const source = data.base_video_url || data.video_url;
                if (source) setPreviewUrl(getApiUrl(source));
                setReady(true);
            })
            .catch(err => { if (!controller.signal.aborted) setError(err.message || 'Could not load the saved media.'); })
            .finally(() => { clearTimeout(timeout); if (!controller.signal.aborted) setLoading(false); });
        return () => { clearTimeout(timeout); controller.abort(); };
    }, [endpoint, jobId, loadAttempt, videoUrl, audioOnly]);

    useEffect(() => {
        if (!playing) return;
        let frame;
        const tick = () => { if (player.current) setPlayhead(player.current.currentTime); frame = requestAnimationFrame(tick); };
        frame = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(frame);
    }, [playing]);

    const ordered = [...placements].sort((a, b) => numericTime(a.start) - numericTime(b.start));
    const editablePlacements = audioOnly ? ordered.filter(item => mediaKind(item) === 'audio') : ordered;
    const validation = placementError(ordered, duration);
    const activeMedia = ordered.filter(item => playhead >= numericTime(item.start) && playhead < numericTime(item.end));
    const activeVisuals = activeMedia.filter(item => mediaKind(item) !== 'audio').sort((a, b) => (a.layer || 0) - (b.layer || 0));
    const replay = replayEffectsPreview(postEffects, playhead);

    const seek = value => {
        const at = Math.max(0, Math.min(duration, Number(value) || 0));
        if (player.current && Number.isFinite(player.current.duration)) player.current.currentTime = at;
        setPlayhead(at);
    };
    const togglePlayback = () => {
        const video = player.current;
        if (!video) return;
        if (video.paused) video.play().catch(() => setError('The video preview could not play. Try pressing play again.'));
        else video.pause();
    };
    const updatePlacement = (id, changes) => {
        setPlacements(items => items.map(item => item.id === id ? { ...item, ...changes } : item));
        setDirty(true);
        onDirtyChange?.();
    };
    const beginDrag = (event, item, handle = null) => {
        if (audioOnly || busy || !item || event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        player.current?.pause();
        setSelectedId(item.id);
        const rect = player.current.parentElement.getBoundingClientRect();
        drag.current = { id: item.id, handle, pointerId: event.pointerId, target: event.currentTarget, clientX: event.clientX, clientY: event.clientY, ...appearance(item), rect };
        event.currentTarget.setPointerCapture(event.pointerId);
    };
    const moveImage = event => {
        const previous = drag.current;
        if (!previous || event.pointerId !== previous.pointerId) return;
        const dx = (event.clientX - previous.clientX) / previous.rect.width * 100;
        const dy = (event.clientY - previous.clientY) / previous.rect.height * 100;
        updatePlacement(previous.id, previous.handle ? resizeBounds(previous, previous.handle, dx, dy) : {
            x: rounded(clamp(previous.x + dx, 0, 100 - previous.width)),
            y: rounded(clamp(previous.y + dy, 0, 100 - previous.height)),
        });
    };
    const endDrag = event => {
        const previous = drag.current;
        if (!previous || event.pointerId !== previous.pointerId) return;
        drag.current = null;
        if (previous.target.hasPointerCapture(previous.pointerId)) previous.target.releasePointerCapture(previous.pointerId);
    };
    const handleUsePlayhead = item => {
        const length = Math.max(0.05, (numericTime(item.end) - numericTime(item.start)) || 2);
        const start = Math.min(playhead, Math.max(0, duration - 0.05));
        updatePlacement(item.id, mediaKind(item) === 'video' ? mediaTimelineChange(item, 'start', Number(start.toFixed(3)))
            : { start: Number(start.toFixed(3)), end: Number(Math.min(duration, start + length).toFixed(3)) });
        setSelectedId(item.id);
    };

    const uploadMedia = async (files, sounds = false) => {
        if (!files.length || busy || loading || !ready) return;
        const invalid = files.find(file => {
            if (sounds) return (!AUDIO_TYPES.test(file.name) && !file.type.startsWith('audio/')) || file.size > 30 * 1024 * 1024;
            const video = VIDEO_TYPES.test(file.name) || file.type.startsWith('video/');
            return video ? file.size > 100 * 1024 * 1024 : (!IMAGE_TYPES.test(file.name) && !IMAGE_MIMES.has(file.type)) || file.size > MAX_IMAGE_BYTES;
        });
        if (invalid) {
            setError(`${invalid.name}: ${sounds ? 'choose an audio file up to 30 MB.' : 'choose an image up to 20 MB or an MP4, MOV, WebM, MKV or M4V video up to 100 MB.'}`);
            return;
        }
        if (files.length + placements.length > 50) {
            setError('You can add up to 50 media placements to a clip.');
            return;
        }
        setUploading(true);
        onProcessingChange?.(true);
        setError('');
        player.current?.pause();
        let nextLayer = Math.max(-1, ...placements.map(item => item.layer || 0)) + 1;
        let cursor = Math.min(playhead, Math.max(0, duration - 0.05));
        try {
            for (const file of files) {
                const body = new FormData();
                body.append('file', file);
                const data = await responseJson(await apiFetch(`${endpoint}/assets`, { method: 'POST', body }));
                if (!data.asset || !mediaUrl(data)) throw new Error('The uploaded media is missing its preview. Please try again.');
                const id = placementId();
                const kind = mediaKind(data);
                const length = Number(data.media_duration || data.duration) || 2;
                const end = Math.min(duration, cursor + (kind === 'image' ? 2 : length));
                const dimensions = kind === 'video' ? { width: data.width, height: data.height } : kind === 'image' ? await imageDimensions(mediaUrl(data)) : null;
                const bounds = imageBounds(DEFAULT_APPEARANCE, dimensions, aspectRatio || 9 / 16);
                const item = { ...bounds, id, layer: nextLayer++, asset: data.asset, kind, media_url: mediaUrl(data), image_url: data.image_url, media_duration: kind === 'image' ? undefined : length, has_audio: data.has_audio, name: file.name, start: Number(cursor.toFixed(3)), end: Number(end.toFixed(3)), source_start: 0, volume: kind === 'audio' ? 1 : 0, animation: 'none', animation_duration: .3 };
                setPlacements(items => [...items, item]);
                setSelectedId(id);
                setDirty(true);
                if (!sounds) cursor = Math.min(end, Math.max(0, duration - 0.05));
            }
        } catch (err) {
            setError(err.message || 'Media upload failed. Please try again.');
        } finally {
            setUploading(false);
            onProcessingChange?.(false);
            if (uploadInput.current) uploadInput.current.value = '';
            if (soundInput.current) soundInput.current.value = '';
        }
    };

    const save = async () => {
        if (validation || busy || loading || !ready || !dirty) return false;
        player.current?.pause();
        setSaving(true);
        setError('');
        onProcessingChange?.(true);
        try {
            const data = await responseJson(await apiFetch(endpoint, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ revision, captions_above_media: captionsAbove, placements: ordered.map(item => ({ id: item.id, layer: item.layer ?? 0, asset: item.asset, name: item.name, kind: mediaKind(item), start: Number(item.start), end: Number(item.end), source_start: mediaOffset(item), volume: mediaVolume(item), animation: item.animation || 'none', animation_duration: Number(item.animation_duration) || .3, animation_elapsed: Number(item.animation_elapsed) || 0, ...appearance(item) })) }),
            }));
            if (!(data.new_video_url || data.video_url)) throw new Error('The updated video was not returned. Please reload this editor.');
            markSaved();
            setDirty(false);
            if (data.revision != null) setRevision(data.revision);
            onApplied(data);
            if (!embedded) onClose();
            return true;
        } catch (err) {
            setError(err.message || 'The media could not be added. Please try again.');
            return false;
        } finally {
            setSaving(false);
            onProcessingChange?.(false);
        }
    };

    const draftValue = { captionsAbove, placements: ordered.map(item => ({ id: item.id, layer: item.layer ?? 0, asset: item.asset, name: item.name, kind: mediaKind(item),
        start: numericTime(item.start), end: numericTime(item.end), source_start: numericTime(item.source_start ?? item.media_start ?? 0),
        ...(mediaKind(item) === 'video' ? { source_out: mediaSourceRange(item).end } : {}),
        volume: mediaVolume(item), animation: item.animation || 'none', animation_duration: Number(item.animation_duration) || .3,
        animation_elapsed: Number(item.animation_elapsed) || 0, ...appearance(item) })) };
    const { markSaved } = useEditorDraft({ value: draftValue, ready: !loading && ready,
        canSave: ready && dirty && !validation && !busy, save, onStateChange: onEditorStateChange });

    return <Modal embedded={embedded} isOpen onClose={() => { if (!busy) onClose(); }} hideClose={busy} size="xl" title={audioOnly ? 'Add audio' : 'Add media'} eyebrow={audioOnly ? 'MUSIC · VOICEOVER · SOUND EFFECTS' : 'IMAGES · VIDEOS · SOUND'} className="broll-modal" footer={<div className="broll-editor-footer">
        <p>{saving ? 'Saving your video…' : 'Sounds mix with your original audio.'}</p>
        <div><button type="button" className="btn-quiet px-4 py-2.5" disabled={busy} onClick={onClose}>{embedded ? 'Done' : 'Cancel'}</button>
            <button type="button" className="btn-primary px-5 py-2.5" disabled={loading || busy || !ready || !dirty || Boolean(validation)} onClick={save}>{saving && <Loader2 size={16} className="animate-spin" />}{saving ? 'Saving…' : 'Save changes'}</button></div>
    </div>}>
        {loading ? <div className="broll-editor-loading"><Loader2 size={24} className="animate-spin" /> Loading clip…</div> : <div className="broll-editor">
            <ClipPreview className="broll-editor-preview" controls={{ time: playhead, duration, playing, muted, disabled: busy || !ready,
                toggle: togglePlayback, seek, toggleMute: () => setMuted(value => !value) }} extra={<>
                <div className={`broll-editor-timeline${audioOnly ? ' broll-editor-audio-timeline' : ''}`} aria-label={audioOnly ? 'Audio tracks on the video timeline' : 'Media placements on the video timeline'}>
                    <div className="broll-editor-timeline-line" style={{ left: `${duration ? playhead / duration * 100 : 0}%` }} />
                    {duration > 0 && editablePlacements.filter(item => Number.isFinite(numericTime(item.start)) && numericTime(item.end) > numericTime(item.start)).map((item, i) => <button key={item.id} type="button" title={`${mediaLabel(item)} ${i + 1}: ${secondsLabel(Number(item.start))} to ${secondsLabel(Number(item.end))}`} aria-label={`Jump to ${mediaLabel(item).toLowerCase()} ${i + 1}`} className={`${selectedId === item.id ? 'selected' : ''} ${mediaKind(item) === 'audio' ? 'sound' : 'visual'}`} style={{ left: `${Math.max(0, Number(item.start) / duration * 100)}%`, width: `${Math.min(100, (Number(item.end) - Number(item.start)) / duration * 100)}%` }} onClick={() => { seek(item.start); setSelectedId(item.id); }}>{mediaKind(item) === 'audio' ? '♪' : i + 1}</button>)}
                </div>
                <p className="broll-editor-help">{audioOnly ? 'Audio is added at the playhead. Set its start, end, source offset, and volume, then save to include it in your video.' : 'Images and videos can overlap. Select a layer in your media list to resize, move, or reorder it. Full-size layers can hide images behind them.'}</p>
            </>}>
                <div className="broll-editor-player" style={{ aspectRatio: aspectRatio || 9 / 16, '--broll-ratio': aspectRatio || 9 / 16 }}>
                    <div className="broll-editor-picture" style={replay.style}>
                    <video ref={player} src={captionsAbove && captionConfig && captionFreeUrl ? captionFreeUrl : previewUrl} muted={muted || replay.muted} playsInline preload="metadata" onTimeUpdate={event => setPlayhead(event.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onLoadedMetadata={event => {
                        const actual = event.currentTarget.duration;
                        if (Number.isFinite(actual)) event.currentTarget.currentTime = Math.min(playhead, actual);
                        if (Number.isFinite(actual) && actual > 0) setDuration(previous => previous > 0 ? previous : actual);
                        const { videoWidth, videoHeight } = event.currentTarget;
                        if (videoWidth > 0 && videoHeight > 0) setAspectRatio(videoWidth / videoHeight);
                    }} onError={() => setError('The video preview could not load. Your media is still available below.')} />
                    <button type="button" className="broll-editor-preview-toggle" aria-label={playing ? 'Pause preview' : 'Play preview'} onClick={togglePlayback}>{!playing && <span><Play size={26} fill="currentColor" /></span>}</button>
                    {((!captionsAbove && captionConfig) || hookConfig) && <CaptionPreviewLayer config={!captionsAbove ? captionConfig : null} hook={hookConfig} time={playhead} duration={duration} aspectRatio={aspectRatio} className="broll-editor-text-underlay" />}
                    {activeVisuals.filter(item => mediaUrl(item)).map(visual => { const activeAppearance = appearance(visual); return <div key={visual.id} className={`broll-editor-image ${selectedId === visual.id && !playing ? 'selected' : ''}`} role="group" onFocus={() => { if (!audioOnly) setSelectedId(visual.id); }} tabIndex={busy || audioOnly ? -1 : 0} aria-label={audioOnly ? "Saved visual layer" : "Media in preview. Drag to move, or use arrow keys. Drag a handle to resize."} style={{ zIndex: visual.layer || 0, left: `${activeAppearance.x}%`, top: `${activeAppearance.y}%`, width: `${activeAppearance.width}%`, height: `${activeAppearance.height}%` }} onPointerDown={event => beginDrag(event, visual)} onPointerMove={moveImage} onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag} onKeyDown={event => {
                        if (audioOnly || busy || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
                        event.preventDefault();
                        const step = event.shiftKey ? 5 : 1;
                        updatePlacement(visual.id, { x: clamp(activeAppearance.x + (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0), 0, 100 - activeAppearance.width), y: clamp(activeAppearance.y + (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0), 0, 100 - activeAppearance.height) });
                    }}>
                        <div className="broll-editor-image-content" style={entranceStyle(visual, playhead)}>{mediaKind(visual) === 'video' ? <SyncedMedia key={visual.id} item={muted || replay.muted ? { ...visual, volume: 0 } : visual} playhead={playhead} playing={playing} className="broll-editor-image-fit" fit={activeAppearance.fit} onPlaybackError={() => setError('This video could not preview in your browser. Try an MP4 video.')} /> : <img draggable="false" src={getApiUrl(mediaUrl(visual))} className="broll-editor-image-fit" style={{ objectFit: activeAppearance.fit === 'stretch' ? 'fill' : activeAppearance.fit }} alt="" />}</div>
                        {!playing && !busy && selectedId === visual.id && RESIZE_HANDLES.map(([handle, label]) => <button key={handle} type="button" className={`broll-editor-resize broll-editor-resize-${handle}`} aria-label={`Resize media from ${label}`} title={`Drag ${label} to resize`} onPointerDown={event => beginDrag(event, visual, handle)} onKeyDown={event => {
                            if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
                            event.preventDefault();
                            event.stopPropagation();
                            const step = event.shiftKey ? 5 : 1;
                            const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
                            const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
                            updatePlacement(visual.id, resizeBounds(activeAppearance, handle, dx, dy));
                        }} />)}
                        {!audioOnly && !playing && <span className="broll-editor-move-hint"><Move size={12} />Drag to move</span>}
                    </div>; })}
                    {activeMedia.filter(item => mediaKind(item) === 'audio').map(item => <SyncedMedia key={item.id} item={muted || replay.muted ? { ...item, volume: 0 } : item} playhead={playhead} playing={playing} onPlaybackError={() => setError('This sound could not preview in your browser. Try an MP3 or WAV file.')} />)}
                    </div>
                    {replay.vignette && <div className="broll-editor-vignette" />}
                    {captionsAbove && captionConfig && <CaptionPreviewLayer config={captionConfig} time={playhead} duration={duration} aspectRatio={aspectRatio} />}
                </div>
            </ClipPreview>
            <div className="broll-editor-images">
                <div className="broll-editor-toolbar"><div><h3>{audioOnly ? 'Your audio tracks' : 'Your media'}</h3><p>{audioOnly ? 'Add music, a voiceover, or sound effects over your video.' : 'Position, animate, and add sound at any moment.'}</p></div></div>
                {!audioOnly && <label className="broll-editor-caption-option"><input type="checkbox" checked={captionsAbove} disabled={busy} onChange={event => { player.current?.pause(); setCaptionsAbove(event.target.checked); setDirty(true); }} /><span><strong>Captions above added media</strong><small>Keep your captions visible over images and videos.</small></span></label>}
                <div className="broll-editor-upload-actions">{!audioOnly && <button type="button" className="btn-quiet" disabled={busy || !ready || duration <= 0} onClick={() => uploadInput.current?.click()}>{uploading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}{uploading ? 'Uploading…' : 'Add images or videos'}</button>}<button type="button" className="btn-quiet" disabled={busy || !ready || duration <= 0} onClick={() => soundInput.current?.click()}>{uploading && audioOnly ? <Loader2 size={16} className="animate-spin" /> : <Music2 size={16} />}{audioOnly ? uploading ? 'Uploading…' : 'Upload audio' : 'Add sound'}</button></div>
                <input ref={uploadInput} type="file" accept={VISUAL_ACCEPT} multiple hidden onChange={event => uploadMedia(Array.from(event.target.files || []))} /><input ref={soundInput} type="file" accept={AUDIO_ACCEPT} multiple hidden onChange={event => uploadMedia(Array.from(event.target.files || []), true)} />
                {!editablePlacements.length && <button type="button" className="broll-editor-empty" disabled={busy || !ready || duration <= 0} onClick={() => (audioOnly ? soundInput : uploadInput).current?.click()}>{audioOnly ? <Music2 size={32} /> : <ImagePlus size={32} />}<strong>{audioOnly ? 'Add audio over your video' : 'Add an image or video'}</strong><span>{audioOnly ? 'Upload a song, voice recording, or sound effect. Preview it with your video and adjust the timing and volume.' : 'Choose its timing and entrance animation, then add a sound effect at the same time.'}</span><small>{audioOnly ? 'MP3, WAV, M4A, AAC, OGG, FLAC, OPUS · Up to 30 MB per file' : 'Images up to 20 MB · Videos up to 100 MB · Sounds up to 30 MB'}</small></button>}
                <div className="broll-editor-list">{editablePlacements.map((item, i) => <div key={item.id} className={`broll-editor-placement ${selectedId === item.id ? 'selected' : ''}`}>
                    <button type="button" className="broll-editor-thumbnail" aria-label={`Preview ${mediaLabel(item).toLowerCase()} ${i + 1}`} onClick={() => { seek(item.start); setSelectedId(item.id); }}>{mediaKind(item) === 'audio' ? <Music2 size={28} /> : mediaKind(item) === 'video' ? <><video src={getApiUrl(mediaUrl(item))} muted playsInline preload="metadata" /><Video size={18} className="broll-editor-kind-icon" /></> : <img src={getApiUrl(mediaUrl(item))} alt={item.name || `Image ${i + 1}`} />}</button>
                    <div className="broll-editor-placement-fields"><div className="broll-editor-placement-heading"><strong title={item.name || `${mediaLabel(item)} ${i + 1}`}>{item.name || `${mediaLabel(item)} ${i + 1}`}</strong><button type="button" aria-label={`Remove ${mediaLabel(item).toLowerCase()} ${i + 1}`} disabled={busy} onClick={() => { setPlacements(items => items.filter(row => row.id !== item.id)); setDirty(true); }}><Trash2 size={16} /></button></div>
                        <div className="broll-editor-times"><label>Timeline start <span>seconds</span><input aria-label={`${mediaLabel(item)} ${i + 1} start time in seconds`} type="number" min="0" max={duration} step="0.01" value={item.start} disabled={busy} onChange={event => updatePlacement(item.id, mediaTimelineChange(item, 'start', event.target.value))} /></label><label>Timeline end <span>seconds</span><input aria-label={`${mediaLabel(item)} ${i + 1} end time in seconds`} type="number" min="0.05" max={duration} step="0.01" value={item.end} disabled={busy} onChange={event => updatePlacement(item.id, mediaTimelineChange(item, 'end', event.target.value))} /></label></div>
                        <button type="button" className="broll-editor-use-time" disabled={busy} onClick={() => handleUsePlayhead(item)}><Clock3 size={13} />Start at playhead · {secondsLabel(playhead)}</button>
                    </div>
                    {selectedId === item.id ? <div className="broll-editor-media-settings">
                        {mediaKind(item) === 'video' && <VideoSourceRange item={item} index={i + 1} disabled={busy} onChange={changes => updatePlacement(item.id, changes)} onPlaySource={() => player.current?.pause()} />}
                        {mediaKind(item) === 'audio' && <label className="broll-editor-field">Source offset <span>Skip into the uploaded sound (seconds)</span><input aria-label={`${mediaLabel(item)} ${i + 1} source offset`} type="number" min="0" max={mediaDuration(item) || undefined} step="0.01" value={item.source_start ?? item.media_start ?? 0} disabled={busy} onChange={event => updatePlacement(item.id, { source_start: event.target.value })} /></label>}
                        {mediaKind(item) !== 'audio' && <>
                            <div className="broll-editor-times"><label>Entrance<select aria-label={`${mediaLabel(item)} ${i + 1} entrance animation`} value={item.animation || 'none'} disabled={busy} onChange={event => updatePlacement(item.id, { animation: event.target.value, animation_elapsed: 0 })}><option value="none">None</option><option value="fade">Fade in</option><option value="slide_up">Slide up</option><option value="zoom_in">Zoom in</option></select></label><label>Duration <span>seconds</span><input aria-label={`${mediaLabel(item)} ${i + 1} animation duration`} type="number" min="0.05" max="5" step="0.05" value={item.animation_duration ?? .3} disabled={busy || !item.animation || item.animation === 'none'} onChange={event => updatePlacement(item.id, { animation_duration: event.target.value, animation_elapsed: 0 })} /></label></div>
                            {item.animation && item.animation !== 'none' && <button type="button" className="broll-editor-use-time" disabled={busy || Boolean(validation)} onClick={() => { seek(item.start); player.current?.play().catch(() => setError('Press play to preview the animation.')); }}><Play size={13} />Preview entrance</button>}
                            <label className="broll-editor-opacity">Opacity <span>{Math.round((item.opacity ?? 1) * 100)}%</span><input aria-label={`${mediaLabel(item)} ${i + 1} opacity percent`} type="range" min="0" max="100" step="1" value={Math.round((item.opacity ?? 1) * 100)} disabled={busy} onChange={event => updatePlacement(item.id, { opacity: Number(event.target.value) / 100 })} /></label>
                        </>}
                        {mediaKind(item) === 'audio' && <label className="broll-editor-opacity">Sound volume <span>{Math.round(mediaVolume(item) * 100)}%{mediaVolume(item) === 0 ? ' · Muted' : ''}</span><input aria-label={`${mediaLabel(item)} ${i + 1} volume percent`} type="range" min="0" max="100" step="1" value={Math.min(100, Math.round(mediaVolume(item) * 100))} disabled={busy} onChange={event => updatePlacement(item.id, { volume: Number(event.target.value) / 100 })} /></label>}
                        {mediaKind(item) !== 'audio' && <button type="button" className="broll-editor-use-time" disabled={busy || placements.length >= 50 || !ready} onClick={() => { player.current?.pause(); seek(item.start); soundInput.current?.click(); }}><Music2 size={13} />Add sound at this entrance</button>}
                    </div> : <button type="button" className="broll-editor-edit-appearance" disabled={busy} onClick={() => { setSelectedId(item.id); seek(item.start); }}>Edit {mediaKind(item) === 'audio' ? 'sound' : 'appearance and animation'}</button>}
                </div>)}</div>
                {editablePlacements.length > 0 && <button type="button" className="broll-editor-clear" disabled={busy} onClick={() => { setPlacements(items => audioOnly ? items.filter(item => mediaKind(item) !== 'audio') : []); setSelectedId(null); setDirty(true); }}>{audioOnly ? 'Remove all audio tracks' : 'Remove all media'}</button>}
            </div>
        </div>}
        {(error || validation) && <div className="broll-editor-error" role="alert"><AlertCircle size={16} /><span>{error || validation}{!ready && !loading && <button type="button" className="block underline mt-2" onClick={() => { setLoading(true); setError(''); setLoadAttempt(value => value + 1); }}>Try loading again</button>}</span></div>}
    </Modal>;
}
