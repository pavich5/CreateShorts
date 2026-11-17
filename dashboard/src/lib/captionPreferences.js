// The saved line budget is authoritative over a preset's default grouping.
export function savedOneWordPreference(style, preset) {
    const maxChars = style?.max_chars;
    if (maxChars != null && Number.isFinite(Number(maxChars)) && Number(maxChars) >= 1) {
        return Number(maxChars) === 1;
    }
    return Boolean(preset?.oneWord);
}
