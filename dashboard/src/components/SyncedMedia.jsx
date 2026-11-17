import { useEffect, useRef } from 'react';
import { getApiUrl } from '../config';
import { mediaKind, mediaOffset, mediaUrl, mediaVolume } from '../lib/mediaEditControls';

// The main clip is the clock: seeking, pausing, and replaying keep every layer aligned.
export default function SyncedMedia({ item, playhead, playing, onPlaybackError, className = '', fit = 'stretch' }) {
    const element = useRef(null);
    const wantedTime = mediaOffset(item) + Math.max(0, playhead - Number(item.start));
    const wanted = useRef({ time: wantedTime, playing });
    wanted.current = { time: wantedTime, playing };
    useEffect(() => {
        const media = element.current;
        if (!media) return;
        const sync = () => {
            const target = wanted.current;
            if (media.readyState > 0 && Math.abs(media.currentTime - target.time) > (target.playing ? .15 : .025)) {
                media.currentTime = target.time;
            }
            media.volume = Math.min(1, Math.max(0, mediaVolume(item)));
            media.muted = media.volume === 0;
            if (target.playing && media.paused) media.play().catch(() => onPlaybackError?.());
            else if (!target.playing && !media.paused) media.pause();
        };
        sync();
        media.addEventListener('loadedmetadata', sync);
        return () => media.removeEventListener('loadedmetadata', sync);
    }, [wantedTime, playing, item, onPlaybackError]);
    const props = { ref: element, src: getApiUrl(mediaUrl(item)), preload: 'auto', onError: onPlaybackError, className };
    return mediaKind(item) === 'audio' ? <audio {...props} /> : <video {...props} style={{ objectFit: fit === 'stretch' ? 'fill' : fit }} playsInline />;
}
