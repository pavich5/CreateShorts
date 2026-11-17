export function clipDurationSeconds(clip, mediaDuration) {
    if (Number.isFinite(mediaDuration) && mediaDuration > 0) return mediaDuration;
    if (Number.isFinite(clip.duration_seconds) && clip.duration_seconds > 0) return clip.duration_seconds;
    const segments = clip.recipe?.segments;
    if (segments?.length) return segments.reduce((total, segment) => total + segment.end - segment.start, 0);
    return Number.isFinite(clip.end) && Number.isFinite(clip.start) ? clip.end - clip.start : NaN;
}

export function formatClipDuration(clip, mediaDuration) {
    const seconds = Math.floor(clipDurationSeconds(clip, mediaDuration));
    if (!Number.isFinite(seconds) || seconds < 0) return null;
    return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
