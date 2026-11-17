import React, { useState, useEffect, useRef } from 'react';
import './result-card-local.css';
import { Download, RotateCcw, Share2, Instagram, Youtube, Video, AlertCircle, Loader2, Check, Wand2, Type, Calendar, Languages, Link2, Scissors, Crosshair, TrendingUp, ImagePlus, Music2, Sparkles } from 'lucide-react';
import { getApiUrl } from '../config';
import { SaveToClips, useCampaigns } from './Campaigns';
import { apiFetch } from '../lib/api';
import { track } from '../lib/analytics';
import SubtitleModal from './SubtitleModal';
import HookModal from './HookModal';
import TranslateModal from './TranslateModal';
import AIEditModal from './AIEditModal';
import BrollModal from './BrollModal';
import VideoEditorPage from './VideoEditorPage';
import { openEditorRoute } from '../lib/editorRoute';
import { clipDurationSeconds, formatClipDuration } from '../lib/clipDuration';
import RemoveVideoButton from './RemoveVideoButton';
import { editPlaybackWindow } from '../lib/aiEditControls';
import { savedCaptionPreview, savedHookPreview } from '../lib/editorPreviewLayers';
import Modal from './ui/Modal';
import SegmentedControl from './ui/SegmentedControl';
import WatermarkModal from './WatermarkModal';
import TikTokDraftNotice from './TikTokDraftNotice';
import { useAuth } from '../contexts/AuthContext';
import { renderInBrowser } from '../lib/renderInBrowser';

const QUIET_BTN = 'local-clip-action group inline-flex items-center justify-center gap-2 rounded-input border border-rule hover:bg-paper3 text-sm font-medium text-ink2 whitespace-nowrap transition-colors disabled:opacity-45 disabled:cursor-not-allowed';

// Follow an async post (request_id from /api/social/post) for about a minute.
// Returns {done, failed}: done once every platform finished, failed lists
// "platform: reason" for the ones that did not. Unknown or slow = neither.
const POST_TERMINAL = new Set(['completed', 'failed', 'not_found']);
async function followPost(requestId, tries = 12, everyMs = 5000) {
    for (let i = 0; i < tries; i++) {
        await new Promise((r) => setTimeout(r, everyMs));
        let d;
        try {
            const r = await apiFetch(`/api/social/post-status/${encodeURIComponent(requestId)}`);
            if (!r.ok) continue;
            d = await r.json();
        } catch {
            continue;
        }
        if (d.status === 'unknown') return { done: false, failed: [] };
        if (!POST_TERMINAL.has(d.status)) continue;
        const failed = (d.results || [])
            .filter((x) => x.success === false)
            .map((x) => `${x.platform}${x.message ? `: ${x.message}` : ''}`);
        if (d.status !== 'completed' && !failed.length) failed.push('every platform');
        return { done: d.status === 'completed', failed };
    }
    return { done: false, failed: [] };
}

const PLATFORM_OPTIONS = [
    { value: 'tiktok', label: 'tiktok', icon: <Video size={16} /> },
    { value: 'instagram', label: 'instagram', icon: <Instagram size={16} /> },
    { value: 'youtube', label: 'youtube', icon: <Youtube size={16} /> },
];


export default function ResultCard({ clip, index, jobId, durable, uploadPostKey, uploadUserId, geminiApiKey, elevenLabsKey, isManaged, onPlay, onPause, onBulkSubtitle, clipCount = 1, bulkProgress, initialState = null, onStateChange, onVideoUpdated, connectedPlatforms = null, onConnectSocials, onUpgrade = null, libraryFileId = null, savedVersion = false, generationPending = false, initialEditorOpen = false, editorOnly = false, onEditorClose }) {
    const [showModal, setShowModal] = useState(false);
    const campaignStore = useCampaigns();
    const [captionChoice, setCaptionChoice] = useState(clip.caption_preset || 'cc_mozi');
    const [captionSettings, setCaptionSettings] = useState(clip.caption_style || null);
    const [showWatermarkModal, setShowWatermarkModal] = useState(false);
    const { billingEnabled } = useAuth();
    const [isPlaying, setIsPlaying] = useState(false);
    const [previewReady, setPreviewReady] = useState(false);
    const videoRef = React.useRef(null);
    const pendingPreviewTime = useRef(editPlaybackWindow(
        clip.ai_edit?.preview_start ?? clip.ai_edit?.operations?.[0]?.start,
        clip.ai_edit?.preview_end ?? clip.ai_edit?.operations?.[0]?.end)?.start ?? null);
    // Pristine base clip (no burned subtitles/hook), stable regardless of how
    // clip.video_url mutates after server edits. Used as the compositing base
    // for the Remotion preview so it never stacks subtitles over an already-
    // subtitled file (double-subtitle bug).
    const stripBurns = (filename) => {
        let f = filename || '', prev;
        do { prev = f; f = f.replace(/^subtitled_\d+_/, '').replace(/^hooked_\d+_/, '').replace(/^hook_/, ''); } while (f !== prev);
        return f;
    };
    const originalVideoUrl = getApiUrl((clip.video_url || '').replace(/[^/]+$/, stripBurns((clip.video_url || '').split('/').pop())));
    const [currentVideoUrl, setCurrentVideoUrl] = useState(getApiUrl(clip.video_url));
    // Where the <video> element pulls its bytes from. The clips are archived to
    // R2 anyway, and R2 egress is free and edge-served, while /videos is served
    // by the same single-worker API process that is running the renders. So play
    // from R2 when possible.
    //
    // ONLY when R2 holds exactly the file the server considers current for this
    // clip (durable.filename === serverVideoFile). Every server-side edit sends
    // input_filename: serverVideoFile and rewrites clip.video_url, but the R2
    // re-archive behind it is fire-and-forget, so for a few seconds the durable
    // copy is the PRE-edit clip. Preferring it blindly would silently show the
    // clip without the subtitles/hook the user just burned.
    //
    // This is display-only: currentVideoUrl remains the source of truth for the
    // download button and for every server operation, so no edit can be routed
    // to the wrong file by this.
    const [durableSrc, setDurableSrc] = useState(null);
    const [durableFailed, setDurableFailed] = useState(false);
    // Switching src reloads the element and restarts playback, so the durable copy
    // is only ever adopted before the user has touched this player. After an edit
    // the chase in App.jsx can land while they are watching the result, and losing
    // their position to save a few seconds of buffering is a bad trade.
    const [hasPlayed, setHasPlayed] = useState(false);

    // A delivered clip is tens of MB, and on a slow link the old silent
    // fetch-then-save took minutes with nothing on screen, which reads as a dead
    // button. Stream it instead and report progress.
    const [, setDownloadPct] = useState(null);

    const downloadClip = async () => {
        try {
            setDownloadPct(0);
            const response = await fetch(currentVideoUrl);
            if (!response.ok) throw new Error('Download failed');
            const total = Number(response.headers.get('content-length')) || 0;
            let blob;
            // No body reader (old browser) or no length to measure against: fall
            // back to the plain path rather than lose the download.
            if (!response.body || !total) {
                blob = await response.blob();
            } else {
                const reader = response.body.getReader();
                const chunks = [];
                let received = 0;
                for (;;) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    chunks.push(value);
                    received += value.length;
                    setDownloadPct(Math.min(99, Math.round((received / total) * 100)));
                }
                blob = new Blob(chunks, { type: 'video/mp4' });
            }
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = `clip-${index + 1}.mp4`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        } catch (err) {
            console.error('Download error:', err);
            window.open(currentVideoUrl, '_blank');
        } finally {
            setDownloadPct(null);
        }
    };
    // Latest file that exists ON THE SERVER (blob: previews don't count).
    // All server-side operations must chain from this, so burned-in edits
    // (subtitles, hooks, effects) never get silently dropped.
    // A reopened project seeds it from the persisted project state.
    const [serverVideoFile, setServerVideoFile] = useState(initialState?.server_file || (clip.video_url || '').split('/').pop());
    const [videoErrored, setVideoErrored] = useState(false);
    const [resolution, setResolution] = useState(null);

    // Adopt the durable copy only while it matches the current server file, and
    // pin the first signed URL seen for that file: /api/history mints a fresh
    // signature on every call, and swapping src mid-playback restarts the video.
    useEffect(() => {
        if (durable?.url && durable.filename && durable.filename === serverVideoFile && !hasPlayed) {
            setDurableSrc((prev) => prev || durable.url);
        } else {
            setDurableSrc(null);
        }
    }, [durable?.url, durable?.filename, serverVideoFile, hasPlayed]);

    // If the local video failed and a durable R2 URL is (now) available, use it.
    // Handles the race where the video errors before the durable URL has loaded.
    // Deliberately NOT version-gated: reaching here means the local file is gone
    // (retention sweep after a reload), so an older durable copy still beats a
    // broken player.
    useEffect(() => {
        if (videoErrored && durable?.url && currentVideoUrl !== durable.url) {
            setCurrentVideoUrl(durable.url);
            setVideoErrored(false);
        }
    }, [videoErrored, durable, currentVideoUrl]);

    // When an external refresh changes this clip's server file (e.g. bulk
    // subtitles applied from another card), adopt it so the card shows the
    // freshly subtitled video instead of a stale one.
    useEffect(() => {
        const serverUrl = getApiUrl(clip.video_url);
        const serverName = (clip.video_url || '').split('/').pop();
        if (serverName && serverName !== serverVideoFile) {
            pendingPreviewTime.current = editPlaybackWindow(
                clip.ai_edit?.preview_start ?? clip.ai_edit?.operations?.[0]?.start,
                clip.ai_edit?.preview_end ?? clip.ai_edit?.operations?.[0]?.end)?.start ?? null;
            setServerVideoFile(serverName);
            setCurrentVideoUrl(serverUrl);
            setDurableFailed(false);
            setHasPlayed(false);
            if (videoRef.current) videoRef.current.load();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [clip.video_url]);

    const [platforms, setPlatforms] = useState({
        tiktok: true,
        instagram: true,
        youtube: true
    });
    const [postTitle, setPostTitle] = useState("");
    const [postDescription, setPostDescription] = useState("");
    const [isScheduling, setIsScheduling] = useState(false);
    const [scheduleDate, setScheduleDate] = useState("");

    const [posting, setPosting] = useState(false);
    const [postResult, setPostResult] = useState(null);
    const [isEditing, setIsEditing] = useState(false);
    const [showEditor, setShowEditor] = useState(initialEditorOpen);
    const [editorNotice, setEditorNotice] = useState('');
    const [isSubtitling, setIsSubtitling] = useState(false);
    const [isHooking, setIsHooking] = useState(false);
    const [isTranslating, setIsTranslating] = useState(false);
    const [editError, setEditError] = useState(null);

    const [clipDuration, setClipDuration] = useState(() => {
        const secs = clipDurationSeconds(clip);
        return Number.isFinite(secs) ? secs : 30;
    });

    // Accumulate Remotion layers across operations. A reopened project restores
    // the layers persisted in its project state, so the next edit composes over
    // them instead of silently dropping previous browser-side work.
    const [activeLayers, setActiveLayers] = useState(initialState?.active_layers || { subtitles: null, hook: null, effects: null });

    // Report edit state upward (debounced sync to the project record). Skip the
    // mount run: only user-driven changes are worth persisting.
    const stateReported = React.useRef(false);
    useEffect(() => {
        if (!stateReported.current) { stateReported.current = true; return; }
        onStateChange?.(index, { activeLayers, serverVideoFile });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeLayers, serverVideoFile]);

    // True when the current server file already carries burned-in content.
    // Browser (Remotion) renders compose over the ORIGINAL clip, so using them
    // here would silently drop those burns — chain via server FFmpeg instead.
    const hasServerBurns = /(^|_)(subtitled|hook|hooked|ai|broll)_/.test(serverVideoFile || '');

    // The hook currently burned into the server file (auto-hook or a manual
    // one). /api/hook REPLACES it; tracked locally so the modal stays honest
    // after edits without refetching the job.
    const [burnedHook, setBurnedHook] = useState(clip.auto_hook?.text || null);
    const [savedHook, setSavedHook] = useState(clip.auto_hook || null);
    const [editorCaptionWords, setEditorCaptionWords] = useState([]);
    const [captionGeneration, setCaptionGeneration] = useState(clip.caption_generation || null);
    useEffect(() => setCaptionGeneration(clip.caption_generation || null), [clip.caption_generation]);

    // Rebuild the saved text layers over the clean picture in every editor tab.
    useEffect(() => {
        if (!showEditor || !jobId || index === undefined) return;
        const controller = new AbortController();
        apiFetch(`/api/clip/${encodeURIComponent(jobId)}/${index}/transcript`, { signal: controller.signal })
            .then(response => response.ok ? response.json() : null)
            .then(data => { if (!controller.signal.aborted) setEditorCaptionWords(data?.captions || []); })
            .catch(() => { if (!controller.signal.aborted) setEditorCaptionWords([]); });
        return () => controller.abort();
    }, [showEditor, jobId, index, currentVideoUrl]);

    // Fetch clip duration from transcript endpoint
    useEffect(() => {
        if (!jobId || index === undefined) return;
        apiFetch(`/api/clip/${jobId}/${index}/transcript`)
            .then(res => res.ok ? res.json() : null)
            .then(data => {
                if (data && data.durationSec) setClipDuration(data.durationSec);
            })
            .catch(() => {});
    }, [jobId, index]);

    // Which platforms the selected profile actually has linked. `null` means
    // unknown (profile list not loaded) — in that case nothing is gated.
    const knownConnections = Array.isArray(connectedPlatforms);
    const noAccountsConnected = knownConnections && connectedPlatforms.length === 0;
    const platformOptions = knownConnections
        ? PLATFORM_OPTIONS.map((o) => (connectedPlatforms.includes(o.value) ? o : { ...o, disabled: true, hint: 'not connected' }))
        : PLATFORM_OPTIONS;

    const handleConnectAccounts = () => {
        setShowModal(false);
        if (onConnectSocials) onConnectSocials();
        else window.open('https://app.upload-post.com', '_blank', 'noopener');
    };

    // Initialize/Reset form when modal opens
    useEffect(() => {
        if (showModal) {
            setPostTitle(clip.video_title_for_youtube_short || "Viral Short");
            setPostDescription(clip.video_description_for_instagram || clip.video_description_for_tiktok || "");
            setIsScheduling(false);
            setScheduleDate("");
            setPostResult(null);
            // Only preselect platforms the profile can actually publish to.
            if (knownConnections) {
                setPlatforms({
                    tiktok: connectedPlatforms.includes('tiktok'),
                    instagram: connectedPlatforms.includes('instagram'),
                    youtube: connectedPlatforms.includes('youtube'),
                });
            }
        }
        // Reset only when the modal opens for a clip; connection changes while
        // it is open must not wipe the user's selection.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [showModal, clip]);

    const handleAutoEdit = async () => {
        setIsEditing(true);
        setEditError(null);
        try {
            const apiKey = geminiApiKey || localStorage.getItem('gemini_key');

            // Managed (paid) users get the Gemini key resolved server-side;
            // only BYOK/self-host needs a local key.
            if (!apiKey && !isManaged) {
                throw new Error("Gemini API Key is missing. Please set it in Settings.");
            }
            const geminiHeaders = apiKey ? { 'X-Gemini-Key': apiKey } : {};

            // Try Remotion effects endpoint first
            const effectsRes = hasServerBurns || showEditor ? null : await apiFetch('/api/effects/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...geminiHeaders
                },
                body: JSON.stringify({
                    job_id: jobId,
                    clip_index: index,
                    input_filename: serverVideoFile
                })
            });

            if (effectsRes && effectsRes.ok) {
                const data = await effectsRes.json();
                if (data.effects && data.effects.segments) {
                    const newLayers = { ...activeLayers, effects: data.effects };
                    setActiveLayers(newLayers);
                    const blobUrl = await renderInBrowser({
                        videoUrl: originalVideoUrl,
                        durationInSeconds: clipDuration,
                        subtitles: newLayers.subtitles,
                        hook: newLayers.hook,
                        effects: newLayers.effects,
                    });
                    setCurrentVideoUrl(blobUrl);
                    if (videoRef.current) videoRef.current.load();
                    return;
                }
            }

            // Fallback: legacy FFmpeg edit endpoint
            const res = await apiFetch('/api/edit', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...geminiHeaders
                },
                body: JSON.stringify({
                    job_id: jobId,
                    clip_index: index,
                    input_filename: serverVideoFile
                })
            });

            if (!res.ok) {
                const errText = await res.text();
                try {
                    const jsonErr = JSON.parse(errText);
                    throw new Error(jsonErr.detail || errText);
                } catch (e) {
                    throw new Error(errText);
                }
            }

            const data = await res.json();
            if (data.new_video_url) {
                setCurrentVideoUrl(getApiUrl(data.new_video_url));
                setServerVideoFile(data.new_video_url.split('/').pop());
                if (videoRef.current) {
                    videoRef.current.load();
                }
            }

        } catch (e) {
            setEditError(e.message);
            setTimeout(() => setEditError(null), 5000);
        } finally {
            setIsEditing(false);
        }
    };

    // Clips are captioned by default, so "no captions" has to be reachable.
    // Nothing is re-encoded: the server still holds the clean file next to the
    // captioned one and just points this clip back at it.
    const handleRemoveSubtitles = async () => {
        setIsSubtitling(true);
        setEditError(null);
        try {
            const res = await apiFetch('/api/subtitle/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    job_id: jobId, clip_index: index, input_filename: serverVideoFile,
                }),
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            if (data.new_video_url) {
                setCaptionGeneration(null);
                if (showEditor) { applyEditorVersion({ ...data, caption_style: null, caption_preset: 'cc_none' }, 'Captions removed'); return true; }
                setCaptionChoice('cc_none');
                setCaptionSettings(null);
                const serverUrl = getApiUrl(data.new_video_url);
                setServerVideoFile(data.new_video_url.split('/').pop());
                const remaining = { ...activeLayers, subtitles: null };
                setActiveLayers(remaining);
                if (remaining.hook || remaining.effects) {
                    setCurrentVideoUrl(await renderInBrowser({
                        videoUrl: serverUrl,
                        durationInSeconds: clipDuration,
                        subtitles: null,
                        hook: remaining.hook,
                        effects: remaining.effects,
                    }));
                } else {
                    setCurrentVideoUrl(serverUrl);
                }
                if (videoRef.current) videoRef.current.load();
                setEditorNotice('Captions saved');
                return true;
            }
            return false;
        } catch (e) {
            setEditError(e.message);
            setTimeout(() => setEditError(null), 5000);
            return false;
        } finally {
            setIsSubtitling(false);
        }
    };

    const handleSubtitle = async (options) => {
        if (options.preset === 'cc_none') return handleRemoveSubtitles();
        setIsSubtitling(true);
        setEditError(null);
        try {
            // Karaoke styles are burned server-side (ASS word-highlight render);
            // the in-browser Remotion path only handles classic styles, and only
            // when the server file has no burned-in content to preserve.
            if (options.remotion && options.style !== 'karaoke' && !hasServerBurns && !showEditor) {
                // Accumulate layer and render all layers together
                const newLayers = { ...activeLayers, subtitles: options.remotion };
                setActiveLayers(newLayers);
                const blobUrl = await renderInBrowser({
                    videoUrl: originalVideoUrl,
                    durationInSeconds: clipDuration,
                    subtitles: newLayers.subtitles,
                    hook: newLayers.hook,
                    effects: newLayers.effects,
                });
                setCurrentVideoUrl(blobUrl);
                if (videoRef.current) videoRef.current.load();
                setEditorNotice('Captions saved');
                return true;
            }

            // Fallback: legacy FFmpeg
            const res = await apiFetch('/api/subtitle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    job_id: jobId,
                    clip_index: index,
                    position: options.position,
                    font_size: options.fontSize,
                    font_name: options.fontName,
                    font_color: options.fontColor,
                    border_color: options.borderColor,
                    border_width: options.borderWidth,
                    bg_color: options.bgColor,
                    bg_opacity: options.bgOpacity,
                    preset: options.preset || null,
                    style: options.style || 'classic',
                    highlight_color: options.highlightColor || '#FFD700',
                    effect: options.effect || 'none',
                    base_opacity: options.baseOpacity ?? 1.0,
                    uppercase: options.uppercase || false,
                    reveal: options.reveal || false,
                    shadow: options.shadow || 0,
                    max_chars: options.maxChars ?? null,
                    max_duration: options.maxDuration ?? null,
                    input_filename: serverVideoFile,
                    // Edited caption text (clip-relative ms); null = server
                    // regenerates from the transcript as before.
                    words: options.captions || null
                })
            });

            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            if (data.new_video_url) {
                setCaptionGeneration(null);
                if (showEditor) { applyEditorVersion({ ...data, caption_style: data.caption_style || {position:options.position,font_size:options.fontSize,font_name:options.fontName,font_color:options.fontColor,border_color:options.borderColor,border_width:options.borderWidth,bg_color:options.bgColor,bg_opacity:options.bgOpacity,style:options.style,effect:options.effect,base_opacity:options.baseOpacity,uppercase:options.uppercase,highlight_color:options.highlightColor,reveal:options.reveal,shadow:options.shadow,max_chars:options.maxChars,max_duration:options.maxDuration}, caption_preset: data.caption_preset || options.preset || captionChoice }, 'Captions saved'); return true; }
                setCaptionChoice(options.preset || captionChoice);
                setCaptionSettings(data.caption_style || {max_chars:options.maxChars,max_duration:options.maxDuration,position:options.position,font_size:options.fontSize,font_name:options.fontName,font_color:options.fontColor,border_color:options.borderColor,border_width:options.borderWidth,bg_color:options.bgColor,bg_opacity:options.bgOpacity,style:options.style,effect:options.effect,base_opacity:options.baseOpacity,uppercase:options.uppercase,highlight_color:options.highlightColor,reveal:options.reveal,shadow:options.shadow});
                const serverUrl = getApiUrl(data.new_video_url);
                setServerVideoFile(data.new_video_url.split('/').pop());
                // Subtitles are burned into the server file now — drop the
                // browser subtitle layer and re-compose any remaining browser
                // layers (hook/effects) over the new file so they aren't lost.
                const remaining = { ...activeLayers, subtitles: null };
                setActiveLayers(remaining);
                if (remaining.hook || remaining.effects) {
                    const blobUrl = await renderInBrowser({
                        videoUrl: serverUrl,
                        durationInSeconds: clipDuration,
                        subtitles: null,
                        hook: remaining.hook,
                        effects: remaining.effects,
                    });
                    setCurrentVideoUrl(blobUrl);
                } else {
                    setCurrentVideoUrl(serverUrl);
                }
                if (videoRef.current) videoRef.current.load();
                setEditorNotice('Captions saved');
                return true;
            }
            return false;
        } catch (e) {
            setEditError(e.message);
            setTimeout(() => setEditError(null), 5000);
            return false;
        } finally {
            setIsSubtitling(false);
        }
    };

    const handleHook = async (hookData) => {
        setIsHooking(true);
        setEditError(null);
        try {
            if (hookData.remotion && !hasServerBurns && !showEditor) {
                // Accumulate layer and render all layers together
                const newLayers = { ...activeLayers, hook: hookData.remotion };
                setActiveLayers(newLayers);
                const blobUrl = await renderInBrowser({
                    videoUrl: originalVideoUrl,
                    durationInSeconds: clipDuration,
                    subtitles: newLayers.subtitles,
                    hook: newLayers.hook,
                    effects: newLayers.effects,
                });
                setCurrentVideoUrl(blobUrl);
                if (videoRef.current) videoRef.current.load();
                setEditorNotice('Hook saved');
                return true;
            }

            // Fallback: legacy FFmpeg
            const payload = typeof hookData === 'string'
                ? { text: hookData, position: 'top', size: 'M' }
                : hookData;

            const res = await apiFetch('/api/hook', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    job_id: jobId,
                    clip_index: index,
                    text: payload.text,
                    position: payload.position,
                    size: payload.size,
                    style: payload.style || 'pill',
                    font: payload.font || null,
                    duration_seconds: payload.remotion?.displayDurationSec ?? null,
                    input_filename: serverVideoFile
                })
            });

            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            if (data.new_video_url) {
                if (showEditor) { applyEditorVersion({ ...data, auto_hook: { ...(data.burned_hook || {}), text: data.burned_hook?.text ?? payload.text, style: payload.style, font: payload.font, position: payload.position, size: payload.size, duration_seconds: payload.remotion?.displayDurationSec } }, 'Hook saved'); return true; }
                setCurrentVideoUrl(getApiUrl(data.new_video_url));
                setServerVideoFile(data.new_video_url.split('/').pop());
                setBurnedHook(data.burned_hook?.text ?? payload.text ?? null);
                setSavedHook(data.burned_hook ? { ...data.burned_hook, size: payload.size || 'M' } : {
                    text: payload.text, style: payload.style, font: payload.font,
                    position: payload.position, size: payload.size, duration_seconds: payload.remotion?.displayDurationSec,
                });
                if (videoRef.current) videoRef.current.load();
                setEditorNotice('Hook saved');
                return true;
            }
            return false;
        } catch (e) {
            setEditError(e.message);
            setTimeout(() => setEditError(null), 5000);
            return false;
        } finally {
            setIsHooking(false);
        }
    };

    // Strip the burned hook (auto-hook or manual) off the server file.
    const handleRemoveHook = async () => {
        setIsHooking(true);
        setEditError(null);
        try {
            const res = await apiFetch('/api/hook', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    job_id: jobId,
                    clip_index: index,
                    remove: true,
                    input_filename: serverVideoFile,
                }),
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            if (data.new_video_url) {
                if (showEditor) { applyEditorVersion({ ...data, auto_hook: null }, 'Hook removed'); return true; }
                setCurrentVideoUrl(getApiUrl(data.new_video_url));
                setServerVideoFile(data.new_video_url.split('/').pop());
                setBurnedHook(null);
                setSavedHook(null);
                if (videoRef.current) videoRef.current.load();
                setEditorNotice('Hook saved');
                return true;
            }
            return false;
        } catch (e) {
            setEditError(e.message);
            setTimeout(() => setEditError(null), 5000);
            return false;
        } finally {
            setIsHooking(false);
        }
    };

    const handleTranslate = async (options) => {
        console.log('[Translate] Starting translation with options:', options);
        setIsTranslating(true);
        setEditError(null);
        try {
            const apiKey = elevenLabsKey;
            console.log('[Translate] API Key available:', !!apiKey);

            if (!apiKey) {
                throw new Error("ElevenLabs API Key is missing. Please set it in Settings.");
            }

            const requestBody = {
                job_id: jobId,
                clip_index: index,
                target_language: options.targetLanguage,
                input_filename: serverVideoFile
            };
            console.log('[Translate] Request body:', requestBody);
            console.log('[Translate] Sending request to /api/translate');

            const res = await apiFetch('/api/translate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-ElevenLabs-Key': apiKey
                },
                body: JSON.stringify(requestBody)
            });

            console.log('[Translate] Response status:', res.status);

            if (!res.ok) {
                const errText = await res.text();
                console.error('[Translate] Error response:', errText);
                try {
                    const jsonErr = JSON.parse(errText);
                    throw new Error(jsonErr.detail || errText);
                } catch (e) {
                    if (e.message !== errText) throw e;
                    throw new Error(errText);
                }
            }

            const data = await res.json();
            console.log('[Translate] Success response:', data);
            if (data.new_video_url) {
                if (showEditor) { applyEditorVersion(data, 'Voice updated'); return true; }
                setCurrentVideoUrl(getApiUrl(data.new_video_url));
                setServerVideoFile(data.new_video_url.split('/').pop());
                if (videoRef.current) {
                    videoRef.current.load();
                }
                setEditorNotice('Voice updated');
                return true;
            }

        } catch (e) {
            console.error('[Translate] Exception:', e);
            setEditError(e.message);
            setTimeout(() => setEditError(null), 5000);
            return false;
        } finally {
            setIsTranslating(false);
        }
    };

    // Managed (cloud plan/trial) users post with the server-side key — no BYOK needed
    const canPost = isManaged || (uploadPostKey && uploadUserId);

    const handlePost = async () => {
        if (!canPost) {
            setPostResult({ success: false, msg: "Missing API Key or User ID." });
            return;
        }

        if (noAccountsConnected) {
            setPostResult({ success: false, msg: "Connect a social account first." });
            return;
        }

        const selectedPlatforms = Object.keys(platforms).filter(k => platforms[k]);
        if (selectedPlatforms.length === 0) {
            setPostResult({ success: false, msg: "Select at least one platform." });
            return;
        }

        if (isScheduling && !scheduleDate) {
            setPostResult({ success: false, msg: "Please select a date and time." });
            return;
        }

        setPosting(true);
        setPostResult(null);

        try {
            const payload = {
                job_id: jobId,
                clip_index: index,
                api_key: uploadPostKey,
                user_id: uploadUserId,
                platforms: selectedPlatforms,
                title: postTitle,
                description: postDescription
            };

            if (isScheduling && scheduleDate) {
                // Convert to ISO-8601
                payload.scheduled_date = new Date(scheduleDate).toISOString();
                // Optional: pass timezone if needed, backend defaults to UTC or we can send user's timezone
                payload.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
            }

            const res = await apiFetch('/api/social/post', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const errText = await res.text();
                let detail = errText;
                try { detail = JSON.parse(errText).detail || errText; } catch { /* not JSON */ }
                throw new Error(detail);
            }

            const sent = await res.json().catch(() => ({}));
            if (isScheduling || !sent?.request_id) {
                setPostResult({ success: true, msg: isScheduling ? "Scheduled successfully!" : "Sent for publishing." });
                setTimeout(() => {
                    setShowModal(false);
                    setPostResult(null);
                }, 3000);
                return;
            }
            // A 200 only means Upload-Post took the file: follow the post to
            // its real end, so a platform that fails says so here.
            setPostResult({ success: true, msg: "Publishing…" });
            const outcome = await followPost(sent.request_id);
            if (outcome.failed.length) {
                setPostResult({ success: false, msg: `Failed on ${outcome.failed.join('; ')}` });
                track('SocialPostFailed', { props: { platforms: selectedPlatforms.join(',') } });
            } else {
                setPostResult({ success: true, msg: outcome.done ? "Posted successfully!" : "Still publishing: check your accounts in a few minutes." });
                setTimeout(() => {
                    setShowModal(false);
                    setPostResult(null);
                }, 3000);
            }

        } catch (e) {
            setPostResult({ success: false, msg: `Failed: ${e.message}` });
        } finally {
            setPosting(false);
        }
    };

    // Browser-rendered previews (Remotion) live in a blob: URL that exists only
    // in this tab, so they always win over the durable copy.
    const playbackUrl = (durableSrc && !durableFailed && !String(currentVideoUrl || '').startsWith('blob:'))
        ? durableSrc
        : currentVideoUrl;

    const durationReadout = formatClipDuration(clip, clipDuration);
    useEffect(() => {
        setPreviewReady((videoRef.current?.readyState || 0) >= 1);
        setVideoErrored(false);
        setIsPlaying(false);
    }, [playbackUrl]);

    const togglePreview = () => {
        const player = videoRef.current;
        if (!player) return;
        if (player.paused) player.play().catch(() => setIsPlaying(false));
        else player.pause();
    };
    const retryPreview = () => {
        setVideoErrored(false);
        setPreviewReady(false);
        videoRef.current?.load();
    };

    const editingBusy = isEditing || isSubtitling || isHooking || isTranslating || Boolean(bulkProgress?.running);
    const closeEditor = () => { if (!editingBusy) { setShowEditor(false); onEditorClose?.({ new_video_url: currentVideoUrl, duration_seconds: clipDuration }); } };
    const editorBaseUrl = currentVideoUrl.startsWith('blob:') ? originalVideoUrl : currentVideoUrl.replace(/[^/]+$/, stripBurns(currentVideoUrl.split('/').pop()));
    const editorHookLayer = activeLayers.hook || savedHookPreview(savedHook, clipDuration);
    const editorCaptionLayer = activeLayers.subtitles || (/(^|_)subtitled_/.test(serverVideoFile || '')
        ? savedCaptionPreview(editorCaptionWords, captionSettings, clip.layout_ranges || []) : null);
    const applyEditorVersion = (data, message = 'Changes saved') => {
        const updated = { ...(data.clip || {}), ...data };
        const nextUrl = data.new_video_url || data.video_url;
        if (!nextUrl) return;
        const duration = updated.duration_seconds ?? updated.duration;
        pendingPreviewTime.current = editPlaybackWindow(data.preview_start, data.preview_end)?.start ?? null;
        setCurrentVideoUrl(getApiUrl(nextUrl));
        setServerVideoFile(nextUrl.split('/').pop().split(/[?#]/)[0]);
        setDurableSrc(null); setDurableFailed(false); setVideoErrored(false); setHasPlayed(false);
        setActiveLayers({ subtitles: null, hook: null, effects: null });
        if (Number.isFinite(duration)) setClipDuration(duration);
        if (updated.auto_hook !== undefined) {
            setBurnedHook(updated.auto_hook?.text || null);
            setSavedHook(previous => updated.auto_hook ? { ...previous, ...updated.auto_hook } : null);
        }
        if (updated.caption_style !== undefined) setCaptionSettings(updated.caption_style);
        if (updated.caption_generation !== undefined) setCaptionGeneration(updated.caption_generation);
        if (updated.caption_preset !== undefined) setCaptionChoice(updated.caption_preset);
        setEditorNotice(message);
        onVideoUpdated?.(index, { ...updated, new_video_url: nextUrl, ...(Number.isFinite(duration) ? { duration_seconds: duration } : {}) });
    };
    const aiPanel = (mode, close, onEditorStateChange) => <AIEditModal onEditorStateChange={onEditorStateChange} embedded initialMode={mode} onClose={close} videoUrl={currentVideoUrl} jobId={jobId} clipIndex={index} fileId={!jobId ? libraryFileId : null} inputFilename={serverVideoFile} onProcessingChange={setIsEditing} onApplied={data => applyEditorVersion(data, mode === 'trim' ? 'Trim saved' : 'AI edit saved')} />;
    const editorSections = [
        { id: 'trim', label: 'Trim', hint: 'Keep or remove parts', description: 'Keep a range, or remove a section and join the remaining footage.', icon: Scissors, render: ({ onClose: close, onEditorStateChange }) => aiPanel('trim', close, onEditorStateChange) },
        { id: 'media', label: 'Media & sound', hint: 'Images, videos, effects', description: 'Add images, videos, entrance animations, and sound effects on your timeline.', icon: ImagePlus, render: ({ onClose: close, onEditorStateChange }) => <BrollModal onEditorStateChange={onEditorStateChange} embedded jobId={libraryFileId ? `file:${libraryFileId}` : jobId} clipIndex={libraryFileId ? 0 : index} videoUrl={currentVideoUrl} durationInSeconds={clipDuration} onClose={close} onProcessingChange={setIsEditing} onApplied={data => applyEditorVersion(data, 'Media saved')} /> },
        { id: 'audio', label: 'Audio', hint: 'Music & sound overlays', description: 'Upload music, a voiceover, or sound effects. Choose when each track plays and adjust its volume over the original video audio.', icon: Music2, render: ({ onClose: close, onEditorStateChange }) => <BrollModal audioOnly onEditorStateChange={onEditorStateChange} embedded jobId={libraryFileId ? `file:${libraryFileId}` : jobId} clipIndex={libraryFileId ? 0 : index} videoUrl={currentVideoUrl} durationInSeconds={clipDuration} onClose={close} onProcessingChange={setIsEditing} onApplied={data => applyEditorVersion(data, 'Audio saved')} /> },
        { id: 'captions', label: 'Captions', hint: 'Text & styling', description: 'Edit the words, choose a look, and preview your captions.', icon: Type, render: ({ active, onClose: close, onEditorStateChange }) => <SubtitleModal key={currentVideoUrl} onEditorStateChange={onEditorStateChange} embedded active={active} isOpen onClose={close} onGenerate={handleSubtitle} onApplyAll={onBulkSubtitle ? async options => { await onBulkSubtitle(options); setEditorNotice('Captions saved'); } : undefined} onRemove={handleRemoveSubtitles} bulkCount={clipCount} bulkProgress={bulkProgress} isProcessing={isSubtitling || Boolean(bulkProgress?.running)} videoUrl={editorBaseUrl} jobId={jobId} clipIndex={index} existingHook={editorHookLayer} layoutRanges={clip.layout_ranges || []} initialPreset={captionChoice} initialStyle={captionSettings} /> },
        { id: 'hook', label: 'Hook', hint: 'Headline & entrance', description: 'Create an opening headline and choose how it appears.', icon: TrendingUp, render: ({ active, onClose: close, onEditorStateChange }) => <HookModal key={currentVideoUrl} onEditorStateChange={onEditorStateChange} embedded active={active} isOpen onClose={close} onGenerate={handleHook} isProcessing={isHooking} videoUrl={editorBaseUrl} initialText={burnedHook || ''} initialHook={savedHook} durationInSeconds={clipDuration} existingSubtitles={editorCaptionLayer} hasCaptions={!!activeLayers.subtitles || /(^|_)subtitled_/.test(serverVideoFile || '')} serverRender={hasServerBurns} burnedHook={burnedHook} onRemove={burnedHook ? handleRemoveHook : null} /> },
        { id: 'ai', label: 'AI edit', hint: 'Describe your changes', description: 'Make a specific change or find a moment in the original footage.', icon: Wand2, render: ({ onClose: close, onEditorStateChange }) => <>{aiPanel('ai', close, onEditorStateChange)}{(billingEnabled || geminiApiKey) && <div className="unified-editor-auto"><p>Let AI choose a set of automatic improvements.</p><button type="button" className="btn-quiet" onClick={handleAutoEdit} disabled={editingBusy}><Wand2 size={15} />Auto edit</button></div>}</> },
        ...((billingEnabled || elevenLabsKey) ? [{ id: 'voice', label: 'Voice', hint: 'Translate & dub', description: 'Translate the voice in your clip.', icon: Languages, render: ({ active, onClose: close, onEditorStateChange }) => <TranslateModal onEditorStateChange={onEditorStateChange} embedded active={active} isOpen onClose={close} onTranslate={handleTranslate} isProcessing={isTranslating} videoUrl={currentVideoUrl} hasApiKey={!!elevenLabsKey} /> }] : []),
    ];

    return (
        <div className={`local-clip-card card overflow-hidden flex flex-col group hover:border-rule2 transition-colors animate-fade ${editorOnly ? 'hidden' : ''}`} style={{ animationDelay: `${index * 0.1}s`, display: editorOnly ? 'none' : undefined }}>
            {/* Large media stage keeps playback and fullscreen controls easy to reach. */}
            <div className="local-clip-stage group/video">
                <video
                    ref={videoRef}
                    src={playbackUrl}
                    preload="metadata"
                    aria-label={`Preview clip ${index + 1}: ${clip.video_title_for_youtube_short || clip.title || "Generated short"}`}
                    className="local-clip-video"
                    playsInline
                    onCanPlay={() => setPreviewReady(true)}
                    onLoadedMetadata={(e) => {
                        setPreviewReady(true);
                        const actualDuration = e.currentTarget.duration;
                        if (Number.isFinite(actualDuration) && actualDuration > 0) setClipDuration(actualDuration);
                        if (e.target.videoWidth) setResolution(`${e.target.videoWidth}×${e.target.videoHeight}`);
                        if (Number.isFinite(pendingPreviewTime.current)) {
                            e.currentTarget.currentTime = Math.min(pendingPreviewTime.current, e.currentTarget.duration);
                            pendingPreviewTime.current = null;
                        }
                    }}
                    onError={() => {
                        // The durable copy is unreachable (signature expired after an
                        // hour on an idle tab, object purged) → serve from the API for
                        // the rest of this card's life.
                        if (playbackUrl === durableSrc) {
                            setDurableFailed(true);
                            return;
                        }
                        // Local /videos/ file gone (e.g. cleaned up after a reload) →
                        // fall back to the durable R2 copy for managed users. If the
                        // durable URL hasn't loaded yet, the effect above retries.
                        if (durable?.url && currentVideoUrl !== durable.url) setCurrentVideoUrl(durable.url);
                        else setVideoErrored(true);
                    }}
                    onPlay={() => {
                        document.querySelectorAll('.local-clip-video').forEach((player) => {
                            if (player !== videoRef.current && !player.paused) player.pause();
                        });
                        setIsPlaying(true);
                        setHasPlayed(true);
                        const currentTime = videoRef.current ? videoRef.current.currentTime : 0;
                        onPlay && onPlay((clip.start || 0) + currentTime);
                    }}
                    onPause={() => { setIsPlaying(false); onPause && onPause(); }}
                    onEnded={() => { setIsPlaying(false); onPause && onPause(); }}
                />
                {!previewReady && !videoErrored && (
                    <div className="local-clip-loading" role="status"><Loader2 size={24} className="animate-spin" /><span>Loading preview…</span></div>
                )}
                {previewReady && !videoErrored && !isEditing && (
                    <button
                        type="button"
                        className="local-clip-toggle"
                        onClick={togglePreview}
                        aria-label={`${isPlaying ? 'Pause' : 'Play'} clip ${index + 1}`}
                    />
                )}
                {videoErrored && (
                    <div className="local-clip-error" role="alert">
                        <AlertCircle size={26} /><strong>Preview unavailable</strong>
                        <span>The video could not be loaded. Try loading it again.</span>
                        <button onClick={retryPreview}><RotateCcw size={16} /> Reload preview</button>
                    </div>
                )}
                <div className="local-clip-badges">
                    {/* Stays the clip's own number, not its rank: the cards are
                        ordered by score, but this is what the downloaded file
                        is called (clip-N.mp4) and what every api call indexes. */}
                    <span className="local-clip-number">
                        Clip {index + 1}
                    </span>
                    {/* A bare number on a thumbnail reads as a duration, a
                        position, anything — it has to name itself and carry
                        its scale, or it is decoration. */}
                    {Number.isFinite(clip.predicted_score) && (
                        <span
                            className="local-clip-score"
                            title="CreateShorts' prediction of how well this clip will perform, from 0 to 100"
                        >
                            <TrendingUp size={13} className="shrink-0" />
                            <span>Score</span>
                            <b className={
                                clip.predicted_score >= 80 ? 'text-ok'
                                    : clip.predicted_score >= 65 ? 'text-brass'
                                        : 'text-ink2'
                            }>
                                {clip.predicted_score}
                            </b>
                            <span>/100</span>
                        </span>
                    )}
                </div>

                {/* Auto Edit Overlay if Processing */}
                {isEditing && (
                    <div className="absolute inset-0 bg-black/70 flex flex-col items-center justify-center z-10 p-4 text-center">
                        <Loader2 size={28} className="text-brass animate-spin mb-3" />
                        <span className="text-sm text-white">Applying edits…</span>
                        <span className="readout mt-1.5 text-white/80">Please keep this page open</span>
                    </div>
                )}
            </div>

            {/* Right: Content & Details */}
            <div className="local-clip-content flex-1 p-4 md:p-5 flex flex-col min-w-0">
                <div className="local-clip-details">
                    <h3 className="local-clip-title text-lg font-semibold text-ink leading-snug line-clamp-2 mb-2 break-words" title={clip.video_title_for_youtube_short}>
                        {clip.video_title_for_youtube_short || clip.title || `Short ${index + 1}`}
                    </h3>
                    {captionGeneration?.status === 'failed' && <p role="status" className="mb-2 text-xs text-warn">Captions weren’t added. Open Captions to try again.</p>}
                    <div className="flex flex-wrap gap-1.5">
                        {durationReadout && <span className="readout bg-paper3 px-2 py-0.5 rounded-full shrink-0">{durationReadout}</span>}
                        {resolution && <span className="readout bg-paper3 px-2 py-0.5 rounded-full shrink-0">{resolution}</span>}

                    </div>
                </div>

                <button type="button" onClick={() => { videoRef.current?.pause(); openEditorRoute(libraryFileId ? `file:${libraryFileId}` : jobId, libraryFileId ? 0 : index, serverVideoFile); }} title={generationPending ? 'Wait until clip generation finishes before editing.' : currentVideoUrl.startsWith('blob:') ? 'Save this browser preview to Clips to keep editing.' : 'Trim, add media and sound, edit captions and hooks, or use AI'} disabled={generationPending || isEditing || isSubtitling || isHooking || isTranslating || currentVideoUrl.startsWith('blob:')} className="btn-primary local-clip-edit w-full py-2.5"><Scissors size={16} />Edit</button>

                {/* Error Message */}
                {editError && (
                    <div className="mb-3 px-3 py-2 rounded-input text-xs text-danger bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] flex items-center gap-2">
                        <AlertCircle size={14} className="shrink-0" />
                        {editError}
                    </div>
                )}

                <div className="local-clip-save"><SaveToClips sourceUrl={currentVideoUrl} alreadySaved={savedVersion} sourceKey={libraryFileId ? 'file/'+libraryFileId : jobId+'/clip/'+index} filename={(clip.video_title_for_youtube_short || 'Short '+(index+1))+'.mp4'} campaignId={(campaignStore.clips[libraryFileId ? 'file/'+libraryFileId : jobId+'/clip/'+index] || campaignStore.clips[jobId+'/project'])} /></div>

                {/* Actions Footer */}
                <div className="local-clip-actions pt-2 border-t border-rule">
                    {(billingEnabled || uploadPostKey) && <button
                        onClick={() => setShowModal(true)}
                        className={`${QUIET_BTN} local-clip-post`}
                    >
                        <Share2 size={16} className="shrink-0" /> Post
                    </button>}
                    <RemoveVideoButton jobId={jobId} clipIndex={index} fileId={libraryFileId} title={clip.video_title_for_youtube_short || clip.title || `Short ${index + 1}`} disabled={generationPending || isEditing || isSubtitling || isHooking || isTranslating || currentVideoUrl.startsWith('blob:')} className={`${QUIET_BTN} local-clip-remove`} onBeforeOpen={() => videoRef.current?.pause()} />

                </div>
            </div>

            {showEditor && <VideoEditorPage title={clip.video_title_for_youtube_short || clip.title || `Short ${index + 1}`} sections={editorSections} version={currentVideoUrl} busy={editingBusy} error={editError} notice={editorNotice} onClose={closeEditor} />}

            {/* Post Modal */}
            <Modal
                isOpen={showModal}
                onClose={() => setShowModal(false)}
                eyebrow="PUBLISH"
                title="post clip"
                size="md"
                footer={
                    noAccountsConnected ? (
                        <button onClick={handleConnectAccounts} className="btn-primary w-full">
                            <Link2 size={16} /> connect accounts
                        </button>
                    ) : (
                        <button
                            onClick={handlePost}
                            disabled={posting || !canPost}
                            className="btn-primary w-full"
                        >
                            {posting ? <><Loader2 size={16} className="animate-spin" /> {isScheduling ? 'scheduling…' : 'publishing…'}</> : <><Share2 size={16} /> {isScheduling ? 'schedule post' : 'publish now'}</>}
                        </button>
                    )
                }
            >
                {!canPost && (
                    <div className="mb-4 px-3 py-2 rounded-input text-xs text-warn bg-[color-mix(in_oklab,var(--color-warn)_10%,transparent)] flex items-start gap-2">
                        <AlertCircle size={14} className="mt-0.5 shrink-0" />
                        <div className="lowercase">configure api key in settings first.</div>
                    </div>
                )}

                {noAccountsConnected && (
                    <div className="mb-4 px-3 py-2 rounded-input text-xs text-warn bg-[color-mix(in_oklab,var(--color-warn)_10%,transparent)] flex items-start gap-2">
                        <AlertCircle size={14} className="mt-0.5 shrink-0" />
                        <div className="lowercase">no social accounts connected yet — link tiktok, instagram or youtube to publish this clip.</div>
                    </div>
                )}

                {/* Both the title/description fields below and the schedule
                    button are downstream of this: on a tiktok draft neither
                    travels. See TikTokDraftNotice. */}
                {platforms.tiktok && <TikTokDraftNotice />}

                <div className="space-y-4">
                    {/* Title & Description */}
                    <div>
                        <label className="eyebrow block mb-1.5">TITLE</label>
                        <input
                            type="text"
                            value={postTitle}
                            onChange={(e) => setPostTitle(e.target.value)}
                            className="input-field"
                            placeholder="enter a catchy title…"
                        />
                    </div>

                    <div>
                        <label className="eyebrow block mb-1.5">CAPTION</label>
                        <textarea
                            value={postDescription}
                            onChange={(e) => setPostDescription(e.target.value)}
                            rows={4}
                            className="input-field resize-none"
                            placeholder="write a caption for your post…"
                        />
                    </div>

                    {/* Scheduling */}
                    <div className="p-3 bg-paper rounded-input border border-rule">
                        <label className="flex items-center justify-between cursor-pointer">
                            <span className="flex items-center gap-2 text-sm text-ink2 lowercase">
                                <Calendar size={16} className={isScheduling ? 'text-brass' : 'text-muted'} /> schedule post
                            </span>
                            <input
                                type="checkbox"
                                checked={isScheduling}
                                onChange={(e) => setIsScheduling(e.target.checked)}
                                className="w-4 h-4 accent-brass cursor-pointer"
                            />
                        </label>

                        {isScheduling && (
                            <div className="mt-3 animate-fade">
                                <label className="eyebrow block mb-1.5">DATE · TIME</label>
                                <input
                                    type="datetime-local"
                                    value={scheduleDate}
                                    onChange={(e) => setScheduleDate(e.target.value)}
                                    className="input-field [color-scheme:dark]"
                                />
                            </div>
                        )}
                    </div>

                    {/* Platforms */}
                    <div>
                        <label className="eyebrow block mb-2">PLATFORMS</label>
                        <SegmentedControl
                            multi
                            columns={3}
                            options={platformOptions}
                            value={Object.keys(platforms).filter(k => platforms[k])}
                            onChange={(arr) => setPlatforms({
                                tiktok: arr.includes('tiktok'),
                                instagram: arr.includes('instagram'),
                                youtube: arr.includes('youtube'),
                            })}
                        />
                    </div>

                    {postResult && (
                        <div className={postResult.success ? 'badge-ok' : 'badge-danger'}>
                            {postResult.success ? <Check size={12} className="shrink-0" /> : <AlertCircle size={12} className="shrink-0" />}
                            {postResult.msg}
                        </div>
                    )}
                </div>
            </Modal>

            {showWatermarkModal && (
                <WatermarkModal
                    source="download"
                    jobId={jobId}
                    onUpgrade={onUpgrade}
                    onClose={() => setShowWatermarkModal(false)}
                    onContinue={downloadClip}
                />
            )}

        </div>
    );
}
