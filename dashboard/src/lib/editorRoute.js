const validTarget = target => typeof target === 'string' && (/^[A-Za-z0-9_-]{1,80}$/.test(target) || /^file:[0-9a-f]{64}$/.test(target));
const validFilename = filename => typeof filename === 'string' && filename !== '.' && filename !== '..' && /^[A-Za-z0-9_.-]{1,255}$/.test(filename);

export function parseEditorRoute(hash) {
    const match = /^#edit\/([^/?]+)\/(\d+)(?:\?(.*))?$/.exec(hash || '');
    if (!match) return null;
    let targetId;
    try { targetId = decodeURIComponent(match[1]); } catch { return null; }
    const clipIndex = Number(match[2]);
    if (!validTarget(targetId) || !Number.isSafeInteger(clipIndex)) return null;
    const filename = new URLSearchParams(match[3] || '').get('input_filename');
    return { targetId, clipIndex, inputFilename: validFilename(filename) ? filename : null };
}

export function editorHash(targetId, clipIndex = 0, inputFilename) {
    if (!validTarget(targetId) || !Number.isSafeInteger(clipIndex) || clipIndex < 0) return null;
    const query = validFilename(inputFilename) ? `?input_filename=${encodeURIComponent(inputFilename)}` : '';
    return `#edit/${encodeURIComponent(targetId)}/${clipIndex}${query}`;
}

export function openEditorRoute(targetId, clipIndex = 0, inputFilename) {
    const hash = editorHash(targetId, clipIndex, inputFilename);
    if (!hash || window.location.hash === hash) return;
    document.querySelectorAll('video,audio').forEach(media => media.pause());
    const method = parseEditorRoute(window.location.hash) ? 'replaceState' : 'pushState';
    window.history[method]({ ...window.history.state, openshortsEditor: true }, '', hash);
    window.dispatchEvent(new Event('hashchange'));
}

export function closeEditorRoute() {
    if (window.history.state?.openshortsEditor && window.history.length > 1) {
        window.history.back();
        return;
    }
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#app`);
    window.dispatchEvent(new Event('hashchange'));
}
