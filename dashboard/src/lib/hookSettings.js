export function hookEditorSettings(hook, text = '', preferences = {}, clipDuration = 30, serverRender = false) {
    const duration = Number.isFinite(Number(clipDuration)) && Number(clipDuration) > 0 ? Number(clipDuration) : 30;
    const recordedDuration = hook?.duration_seconds ?? hook?.displayDurationSec;
    const displayDuration = hook ? (recordedDuration == null ? duration : Number(recordedDuration)) : Math.min(5, duration);
    return {
        text: hook?.text ?? text,
        position: hook ? hook.position || 'auto' : preferences.pos || 'auto',
        size: hook ? hook.size || 'M' : preferences.size || 'M',
        style: hook ? hook.style || 'pill' : preferences.style || 'pill',
        font: hook ? hook.font || null : preferences.font || null,
        entranceAnimation: serverRender || hook ? hook?.entranceAnimation || 'none' : preferences.entranceAnimation || 'spring',
        displayDuration: Math.max(.05, Math.min(duration, Number.isFinite(displayDuration) ? displayDuration : duration)),
    };
}
