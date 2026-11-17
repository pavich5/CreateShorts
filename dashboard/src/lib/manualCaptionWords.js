export function retimeCaptionText(text, durationSeconds, originalCaptions = []) {
    const words = text.trim().split(/\s+/u).filter(Boolean);
    const durationMs = Math.floor(Number(durationSeconds) * 1000);
    if (!words.length || words.length > 2000 || !Number.isFinite(durationMs) || durationMs <= 0) return [];
    // A word correction changes text, not the recognized speech timing.
    // Redistributing these words evenly erased pauses and moved later captions.
    const groupSizes = originalCaptions.map(caption => String(caption.text || '').trim().split(/\s+/u).filter(Boolean).length);
    if (groupSizes.every(size => size > 0) && words.length === groupSizes.reduce((sum, size) => sum + size, 0)) {
        let cursor = 0;
        // Recognition can give several untimed tokens one shared interval.
        // Keep that group instead of assigning invented per-token timing.
        const timed = originalCaptions.map((caption, index) => {
            const end = cursor + groupSizes[index];
            const text = words.slice(cursor, end).join(' ');
            cursor = end;
            return {
                text,
                startMs: Math.max(0, Math.floor(Number(caption.startMs))),
                endMs: Math.min(durationMs, Math.floor(Number(caption.endMs))),
                ...(caption.utteranceId !== undefined ? { utteranceId: caption.utteranceId } : {}),
            };
        });
        if (timed.every((word, index) => Number.isFinite(word.startMs) && Number.isFinite(word.endMs)
            && word.endMs > word.startMs && (!index || word.startMs >= timed[index - 1].startMs))) return timed;
    }
    const originalStart = Number(originalCaptions[0]?.startMs);
    const originalEnd = Number(originalCaptions.at(-1)?.endMs);
    const hasOriginalRange = Number.isFinite(originalStart) && Number.isFinite(originalEnd) && originalEnd > originalStart;
    const startMs = hasOriginalRange ? Math.max(0, Math.min(durationMs, Math.floor(originalStart))) : 0;
    const endMs = hasOriginalRange ? Math.min(durationMs, Math.floor(originalEnd)) : durationMs;
    const span = endMs - startMs;
    if (span < words.length) return [];
    return words.map((word, index) => ({
        text: word,
        startMs: Math.floor(startMs + index * span / words.length),
        endMs: Math.floor(startMs + (index + 1) * span / words.length),
    }));
}
