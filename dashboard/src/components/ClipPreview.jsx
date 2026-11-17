import { useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Clock3, Film, Maximize2, Pause, Play, Volume2, VolumeX } from 'lucide-react';
import { formatTimestamp } from '../lib/aiEditControls';
import './clip-preview.css';

// Shared presentation for native video, live caption/hook compositions and
// editable media layers. Each renderer supplies the same playback interface.
export default function ClipPreview({ children, controls, actions, selection, hint, className = '', extra }) {
    const preview = useRef(null);
    const { time = 0, duration = 0, playing, muted, disabled, toggle, seek, toggleMute } = controls;
    const fullscreen = () => {
        const result = preview.current?.requestFullscreen?.();
        result?.catch?.(() => {});
    };
    const percent = value => duration > 0 ? Math.max(0, Math.min(100, value / duration * 100)) : 0;
    return <section ref={preview} className={`clip-preview ${className}`} aria-label="Clip preview and timeline">
        <div className="clip-preview-heading"><span><Film size={15} />Clip preview</span>{actions}</div>
        <div className="clip-preview-stage">{children}</div>
        <div className="clip-preview-toolbar">
            <div>
                <button type="button" aria-label={playing ? 'Pause preview' : 'Play preview'} disabled={disabled || !duration} onClick={toggle}>{playing ? <Pause size={18} /> : <Play size={18} />}</button>
                <button type="button" aria-label="Back one second" disabled={disabled || !duration} onClick={() => seek(time - 1)}><ChevronLeft size={18} /></button>
                <button type="button" aria-label="Forward one second" disabled={disabled || !duration} onClick={() => seek(time + 1)}><ChevronRight size={18} /></button>
            </div>
            <output aria-label="Preview timestamp">{formatTimestamp(time)}<span> / {formatTimestamp(duration)}</span></output>
            <div>
                <button type="button" aria-label={muted ? 'Unmute preview' : 'Mute preview'} onClick={toggleMute}>{muted ? <VolumeX size={17} /> : <Volume2 size={17} />}</button>
                <button type="button" aria-label="Full screen preview" onClick={fullscreen}><Maximize2 size={16} /></button>
            </div>
        </div>
        <div className="clip-preview-timeline">
            <div className="clip-preview-track">
                {selection && <span className="clip-preview-selection" style={{ left: `${percent(selection.start)}%`, width: `${Math.max(0, percent(selection.end) - percent(selection.start))}%` }} />}
                <span className="clip-preview-progress" style={{ width: `${percent(time)}%` }} />
                <input type="range" aria-label="Seek preview" min="0" max={duration || 0} step="0.01" value={Math.max(0, Math.min(time, duration || 0))} disabled={disabled || !duration} onChange={event => seek(Number(event.target.value))} />
            </div>
            <div className="clip-preview-ticks" aria-hidden="true">{[0, .25, .5, .75, 1].map(fraction => <span key={fraction}>{formatTimestamp(duration * fraction)}</span>)}</div>
        </div>
        <div className="clip-preview-detail">{extra || <p><Clock3 size={12} />{hint || 'Scrub to any moment in your clip.'}</p>}</div>
    </section>;
}

export function NativeClipPreview({ videoUrl, onLoadedMetadata, className = '' }) {
    const video = useRef(null);
    const [time, setTime] = useState(0);
    const [duration, setDuration] = useState(0);
    const [playing, setPlaying] = useState(false);
    const [muted, setMuted] = useState(false);
    const [error, setError] = useState(false);
    const toggle = () => { if (video.current?.paused) video.current.play().catch(() => {}); else video.current?.pause(); };
    return <ClipPreview className={className} controls={{ time, duration, playing, muted, disabled: error, toggle,
        seek: at => { if (video.current) video.current.currentTime = Math.max(0, Math.min(duration, at)); }, toggleMute: () => setMuted(value => !value) }}>
        <video ref={video} src={videoUrl} playsInline muted={muted} preload="metadata" onLoadStart={() => { setTime(0); setPlaying(false); setError(false); }}
            onLoadedMetadata={event => { setDuration(event.currentTarget.duration); onLoadedMetadata?.(event); }}
            onTimeUpdate={event => setTime(event.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setError(true)} />
        {!playing && !error && <button type="button" className="clip-preview-play" aria-label="Play preview" onClick={toggle}><Play size={25} fill="currentColor" /></button>}
        {error && <div className="clip-preview-message" role="alert">Preview unavailable<button type="button" onClick={() => video.current?.load()}>Retry preview</button></div>}
    </ClipPreview>;
}
