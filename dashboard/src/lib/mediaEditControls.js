export const mediaKind = item => item.kind || item.media_type || 'image';
export const mediaUrl = item => item.media_url || item.image_url;
export const mediaLabel = item => ({ image: 'Image', video: 'Video', audio: 'Sound' })[mediaKind(item)] || 'Media';
export const mediaOffset = item => Number(item.source_start ?? item.media_start) || 0;
export const mediaVolume = item => mediaKind(item) === 'video' ? 0 : Number(item.volume ?? (mediaKind(item) === 'audio' ? 1 : 0));
export const mediaDuration = item => Number(item.media_duration) || (mediaKind(item) !== 'image' ? Number(item.asset_duration) : 0) || 0;
const timeNumber = value => value === '' ? NaN : Number(value);
const timePrecision = value => Number(value.toFixed(3));

export function mediaSourceRange(item) {
    const start = timeNumber(item.source_start ?? item.media_start ?? 0);
    const end = item.source_out != null ? timeNumber(item.source_out)
        : start + timeNumber(item.end) - timeNumber(item.start);
    return { start, end: Number.isFinite(end) ? timePrecision(end) : end };
}

export function videoSourceRangeChange(item, edge, value) {
    const previous = mediaSourceRange(item);
    let start = edge === 'start' ? timeNumber(value) : previous.start;
    let end = edge === 'end' ? timeNumber(value) : previous.end;
    if (edge === 'start' && Number.isFinite(start) && end <= start) {
        end = Math.min(mediaDuration(item) || Infinity, start + Math.max(.05, previous.end - previous.start));
    }
    const changes = { source_start: edge === 'start' && value === '' ? '' : start,
        source_out: edge === 'end' && value === '' ? '' : end };
    if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        changes.end = timePrecision(Number(item.start) + end - start);
    }
    return changes;
}

export function mediaTimelineChange(item, edge, value) {
    if (mediaKind(item) !== 'video') return { [edge]: value };
    const range = mediaSourceRange(item);
    const numeric = timeNumber(value);
    if (edge === 'start') return { start: value,
        ...(Number.isFinite(numeric) && Number.isFinite(range.end - range.start)
            ? { end: timePrecision(numeric + range.end - range.start), source_out: range.end } : {}) };
    return { end: value, source_out: Number.isFinite(numeric)
        ? timePrecision(range.start + numeric - Number(item.start)) : '' };
}

export function placementError(placements, duration) {
    for (const item of placements) {
        const start = item.start === '' ? NaN : Number(item.start);
        const end = item.end === '' ? NaN : Number(item.end);
        if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end - start < .05 - 1e-6 || end > duration + .001) {
            return 'Choose a start and end within the clip, at least 0.05 seconds apart.';
        }
        if (mediaKind(item) !== 'image') {
            const offset = item.source_start === '' ? NaN : Number(item.source_start ?? item.media_start ?? 0);
            if (!Number.isFinite(offset) || offset < 0 || (mediaDuration(item) && offset + end - start > mediaDuration(item) + .025)) {
                return 'The selected video or sound is too short for this range. Adjust its end or source offset.';
            }
            if (mediaKind(item) === 'video') {
                const range = mediaSourceRange(item);
                if (!Number.isFinite(range.end) || range.end - range.start < .05 - 1e-6
                    || (mediaDuration(item) && range.end > mediaDuration(item) + .025)
                    || Math.abs(range.end - range.start - (end - start)) > .002) {
                    return 'Choose a source in and out within the uploaded video, at least 0.05 seconds apart.';
                }
            }
        }
    }
    return '';
}

export function entranceStyle(item, playhead) {
    const length = Number(item.animation_duration) || .3;
    const progress = Math.max(0, Math.min(1, (playhead - Number(item.start) + (Number(item.animation_elapsed) || 0)) / length));
    const animation = item.animation || 'none';
    return {
        opacity: Number(item.opacity ?? 1) * (['fade', 'zoom_in'].includes(animation) ? progress : 1),
        transform: animation === 'slide_up' ? `translateY(${(1 - progress) * 25}%)`
            : animation === 'zoom_in' ? `scale(${.75 + .25 * progress})` : undefined,
    };
}

export function replayEffectsPreview(batches, playhead) {
    let zoom = 1, offsetY = 0, muted = false, vignette = false;
    const filters = [];
    for (const batch of batches || []) {
        let batchZoom = 1;
        for (const effect of batch) {
            const effectTime = playhead + (Number(effect.timeline_offset) || 0);
            if (effectTime < effect.start || effectTime >= effect.end) continue;
            const strength = Number(effect.strength) || 0;
            const progress = Math.max(0, Math.min(1, (effectTime - effect.start) / (effect.end - effect.start)));
            if (effect.type === 'mute') muted = true;
            else if (effect.type === 'punch_in') batchZoom += strength;
            else if (effect.type === 'zoom_in') batchZoom += strength * progress;
            else if (effect.type === 'zoom_pulse') batchZoom += strength * (1 - Math.abs(progress * 2 - 1));
            else if (effect.type === 'color_pop') filters.push(`contrast(${1 + .15 * strength}) saturate(${1 + .6 * strength})`);
            else if (effect.type === 'bw_moment') filters.push('grayscale(1)');
            else if (effect.type === 'brighten') filters.push(`brightness(${1 + .4 * strength})`);
            else if (effect.type === 'darken') filters.push(`brightness(${1 - .4 * strength})`);
            else if (effect.type === 'vignette') vignette = true;
            else if (effect.type === 'flash' && effectTime < effect.start + .15) filters.push('brightness(1.35)');
        }
        offsetY = offsetY * batchZoom + Math.min(0, .5 - .45 * batchZoom);
        zoom *= batchZoom;
    }
    return { muted, vignette, style: { transform: zoom > 1 ? `translateY(${offsetY * 100}%) scale(${zoom})` : undefined, transformOrigin: '50% 0', filter: filters.join(' ') || undefined } };
}
