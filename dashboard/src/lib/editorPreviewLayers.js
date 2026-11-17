const numberOr = (value, fallback) => value != null && Number.isFinite(Number(value)) ? Number(value) : fallback;

export function savedHookPreview(hook, clipDuration) {
    if (!hook?.text?.trim()) return null;
    const duration = Math.max(.05, numberOr(clipDuration, 30));
    const requested = numberOr(hook.duration_seconds ?? hook.displayDurationSec, duration);
    return {
        text: hook.text,
        style: hook.style || 'pill',
        font: hook.font || (hook.style === 'pill' || !hook.style ? 'montserrat' : 'serif'),
        position: hook.position || 'auto',
        size: hook.size || 'M',
        entranceAnimation: hook.entranceAnimation || 'none',
        exitFade: false,
        displayDurationSec: Math.max(.05, Math.min(duration, requested)),
        ...(Number.isFinite(hook.y) ? { y: hook.y } : {}),
        ...(Number.isFinite(hook.scale) ? { scale: hook.scale } : {}),
        ...(Number.isFinite(hook.height) ? { height: hook.height } : {}),
    };
}

export function savedCaptionPreview(captions, settings = {}, layoutRanges = []) {
    if (!captions?.length || settings?.effect === 'clipcutter:none') return null;
    const saved = settings || {};
    const effect = saved.effect || 'pop';
    const karaoke = (saved.style || 'karaoke') === 'karaoke';
    const fontSize = numberOr(saved.font_size, 44);
    const highlightColor = saved.highlight_color || '#FFE500';
    const color = /^#?([a-f\d]{6})$/iu.exec(highlightColor)?.[1];
    const channels = color ? [0, 2, 4].map(offset => parseInt(color.slice(offset, offset + 2), 16)) : [255, 255, 255];
    const lightHighlight = (.299 * channels[0] + .587 * channels[1] + .114 * channels[2]) / 255 > .6;
    const fontName = saved.font_name || 'Anton';
    const lineBudget = Math.max(6, Math.round(({ Anton: 16, 'Montserrat ExtraBold': 9, Impact: 16 }[fontName] || 14) * 44 / fontSize));
    return {
        captions,
        captionEffect: effect,
        wordsPerCaption: Number(saved.max_chars) === 1 ? 1 : 4,
        layoutRanges,
        position: saved.position || saved.alignment || 'bottom',
        maxChars: numberOr(saved.max_chars, lineBudget),
        maxDurationMs: numberOr(saved.max_duration, 1.4) * 1000,
        style: {
            fontFamily: fontName,
            fontSize: effect.startsWith('clipcutter:') ? fontSize : Math.round(fontSize * .85 * 3.85),
            fontColor: saved.font_color || '#FFFFFF',
            highlightColor,
            borderColor: saved.border_color || '#000000',
            borderWidth: numberOr(saved.border_width, 4) * (effect.startsWith('clipcutter:') ? 1 : 1.5),
            bgColor: saved.bg_color || '#000000',
            bgOpacity: numberOr(saved.bg_opacity, 0),
            animation: karaoke ? ({ pop: 'pop', glow: 'word-highlight', highlight: 'karaoke', box: 'karaoke' }[effect] || 'none') : 'none',
            baseOpacity: karaoke ? numberOr(saved.base_opacity, 1) : 1,
            uppercase: karaoke ? Boolean(saved.uppercase ?? true) : false,
            reveal: karaoke && Boolean(saved.reveal),
            shadow: karaoke ? numberOr(saved.shadow, 0) : 0,
            highlightTextColor: lightHighlight ? '#000000' : saved.font_color || '#FFFFFF',
        },
    };
}
