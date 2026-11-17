import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Player } from '@remotion/player';
import { Play } from 'lucide-react';
import { ShortVideo } from '../remotion/compositions/ShortVideo';
import ClipPreview from './ClipPreview';

/** Live caption and hook layers with the same transport as every editor tool. */
export default function RemotionPreview({ videoUrl, durationInSeconds = 30, subtitles = null, hook = null, effects = null, className = '' }) {
    const fps = 30;
    const player = useRef(null);
    const [time, setTime] = useState(0);
    const [playing, setPlaying] = useState(false);
    const [muted, setMuted] = useState(false);
    const [failed, setFailed] = useState(false);
    const [retry, setRetry] = useState(0);
    const durationInFrames = Math.max(1, Math.round(durationInSeconds * fps));
    const duration = durationInFrames / fps;
    const inputProps = useMemo(() => ({ videoUrl, durationInFrames, fps, width: 1080, height: 1920, subtitles, hook, effects }),
        [videoUrl, durationInFrames, subtitles, hook, effects]);
    useEffect(() => {
        const current = player.current;
        if (!current) return;
        const listeners = {
            frameupdate: event => setTime(event.detail.frame / fps),
            play: () => setPlaying(true), pause: () => setPlaying(false), ended: () => setPlaying(false),
            mutechange: event => setMuted(event.detail.isMuted), error: () => setFailed(true),
        };
        Object.entries(listeners).forEach(([name, listener]) => current.addEventListener(name, listener));
        return () => Object.entries(listeners).forEach(([name, listener]) => current.removeEventListener(name, listener));
    }, [videoUrl, retry]);
    useEffect(() => { player.current?.pause(); player.current?.seekTo(0); setTime(0); setPlaying(false); setMuted(false); setFailed(false); }, [videoUrl, retry]);
    const toggle = () => player.current?.toggle();
    return <ClipPreview className={className} controls={{ time, duration, playing, muted, disabled: failed, toggle,
        seek: at => player.current?.seekTo(Math.max(0, Math.min(durationInFrames - 1, Math.round(at * fps)))),
        toggleMute: () => { if (player.current?.isMuted()) player.current.unmute(); else player.current?.mute(); } }}>
        <Player key={`${videoUrl}-${retry}`} ref={player} component={ShortVideo} inputProps={inputProps} durationInFrames={durationInFrames} fps={fps}
            compositionWidth={1080} compositionHeight={1920} className="remotion-preview-picture"
            style={{ width: '100%', height: '100%' }} controls={false} clickToPlay={false} doubleClickToFullscreen={false} />
        {!playing && !failed && <button type="button" className="clip-preview-play" aria-label="Play preview" onClick={toggle}><Play size={25} fill="currentColor" /></button>}
        {failed && <div className="clip-preview-message" role="alert">Preview unavailable<button type="button" onClick={() => { setFailed(false); setRetry(value => value + 1); }}>Retry preview</button></div>}
    </ClipPreview>;
}
