import { useEffect, useRef, useState } from 'react';
import { Play } from 'lucide-react';
import { getApiUrl } from '../config';
import { mediaDuration, mediaSourceRange, mediaUrl, videoSourceRangeChange } from '../lib/mediaEditControls';

export default function VideoSourceRange({ item, index, disabled, onChange, onPlaySource }) {
    const video = useRef(null);
    const stopAt = useRef(null);
    const [time, setTime] = useState(0);
    const [error, setError] = useState('');
    const range = mediaSourceRange(item);
    const duration = mediaDuration(item);
    const valid = [range.start, range.end].every(Number.isFinite) && range.start >= 0
        && range.end - range.start >= .05 - 1e-6 && (!duration || range.end <= duration + .025);
    useEffect(() => {
        if (Number.isFinite(range.start) && video.current?.readyState > 0 && video.current.paused) {
            video.current.currentTime = range.start;
        }
    }, [range.start]);
    const update = (edge, value) => onChange(videoSourceRangeChange(item, edge, value));
    const preview = () => {
        if (!valid || !video.current) return;
        setError('');
        onPlaySource?.();
        stopAt.current = range.end;
        video.current.currentTime = range.start;
        video.current.play().catch(() => { stopAt.current = null; setError('Press play on the uploaded video to preview it.'); });
    };
    return <fieldset className="broll-source-range" disabled={disabled}>
        <legend>Choose a part of the uploaded video</legend>
        <p>Source in and out refer to the uploaded video. This part plays at the timeline start above.</p>
        <video ref={video} src={getApiUrl(mediaUrl(item))} controls playsInline muted preload="metadata"
            aria-label={`Uploaded video ${index} source preview`} onPlay={onPlaySource}
            onLoadedMetadata={event => { if (Number.isFinite(range.start)) event.currentTarget.currentTime = range.start; }}
            onPause={() => { stopAt.current = null; }} onTimeUpdate={event => {
                const current = event.currentTarget;
                if (stopAt.current != null && current.currentTime >= stopAt.current) {
                    const end = stopAt.current;
                    stopAt.current = null;
                    current.pause();
                    current.currentTime = end;
                }
                setTime(current.currentTime);
            }} onError={() => setError('This uploaded video could not be previewed. Try an MP4 file.')} />
        <div className="broll-editor-times">
            <label>Source in <span>seconds</span><input aria-label={`Video ${index} source in seconds`} type="number" min="0" max={duration || undefined} step="0.01" value={item.source_start ?? item.media_start ?? 0} onChange={event => update('start', event.target.value)} /><button type="button" className="broll-editor-use-time" onClick={() => update('start', Number(time.toFixed(3)))}>Set in here · {time.toFixed(2)}s</button></label>
            <label>Source out <span>seconds</span><input aria-label={`Video ${index} source out seconds`} type="number" min="0.05" max={duration || undefined} step="0.01" value={item.source_out ?? range.end} onChange={event => update('end', event.target.value)} /><button type="button" className="broll-editor-use-time" onClick={() => update('end', Number(time.toFixed(3)))}>Set out here · {time.toFixed(2)}s</button></label>
        </div>
        {valid ? <p className="broll-source-summary">Using {range.start.toFixed(2)}–{range.end.toFixed(2)}s · {(range.end - range.start).toFixed(2)}s selected{duration ? ` · ${duration.toFixed(2)}s uploaded` : ''}</p>
            : <p className="broll-source-error" role="alert">Choose an out point after the in point, within the uploaded video.</p>}
        <button type="button" className="broll-editor-use-time" disabled={!valid} onClick={preview}><Play size={13} />Preview selected part</button>
        {error && <p className="broll-source-error" role="alert">{error}</p>}
    </fieldset>;
}
