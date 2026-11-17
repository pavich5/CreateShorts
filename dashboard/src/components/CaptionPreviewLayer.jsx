import { useEffect, useRef } from 'react';
import { Player } from '@remotion/player';
import { HookOverlay } from '../remotion/compositions/HookOverlay';
import { Subtitles } from '../remotion/compositions/Subtitles';

function TextLayers({ config, hook }) {
    return <>{config && <Subtitles config={config} />}{hook && <HookOverlay config={hook} />}</>;
}

export default function CaptionPreviewLayer({ config, hook, time, duration, aspectRatio, className = '' }) {
    const player = useRef(null);
    const frames = Math.max(1, Math.ceil(duration * 30));
    useEffect(() => {
        player.current?.seekTo(Math.max(0, Math.min(frames - 1, Math.floor(time * 30))));
    }, [time, frames]);
    return <div className={`broll-editor-caption-layer ${className}`} aria-hidden="true"><Player ref={player} component={TextLayers}
        inputProps={{ config, hook }} durationInFrames={frames} fps={30} compositionHeight={1920}
        compositionWidth={Math.round(1920 * (aspectRatio || 9 / 16))} controls={false}
        style={{ width: '100%', height: '100%', background: 'transparent' }} /></div>;
}
