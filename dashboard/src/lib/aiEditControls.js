export function formatTimestamp(seconds) {
  if (!Number.isFinite(seconds)) return '--:--';
  const tenths = Math.floor(Math.max(0, seconds) * 10 + 0.000001);
  return `${Math.floor(tenths / 600)}:${String(Math.floor(tenths / 10) % 60).padStart(2, '0')}.${tenths % 10}`;
}

export function validEditRange(start, end, duration) {
  return [start, end, duration].every(Number.isFinite)
    && start >= 0 && end <= duration && end - start >= 0.15;
}

export function validTrimRange(start, end, duration) {
  return validEditRange(start, end, duration)
    && (start > 0 || end < duration - 0.000001);
}

export function editPlaybackWindow(start, end) {
  if (![start, end].every(Number.isFinite) || end <= start) return null;
  const margin = Math.min(0.1, (end - start) / 3);
  return { start: start + margin, end: end - margin };
}

export function scopedInstructions(instructions, range) {
  if (!range) return instructions.trim();
  return `${instructions.trim()}\n\nApply these changes ONLY from ${range.start.toFixed(3)} to ${range.end.toFixed(3)} seconds on this short's timeline. Leave everything outside this selected range unchanged. This selected range takes precedence over any other timestamps in the description.`;
}

export function aiEditDraft(mode, values) {
  if (mode === 'trim') return { start: values.trimStart, end: values.trimEnd, ...(values.trimMode === 'remove' ? { mode: 'remove' } : {}) };
  const range = values.rangeMode ? { start: values.rangeStart, end: values.rangeEnd } : null;
  return { instructions: values.instructions.trim(), range };
}
