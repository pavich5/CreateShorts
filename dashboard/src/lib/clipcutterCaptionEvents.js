// Mirrors clipcutter_captions.caption_events. Keep the backend fixture test in
// sync when changing phrase limits or the treatment of real speech pauses.
export function clipcutterCaptionEvents(captions, maxWords = 4) {
    const utterances = [];
    for (const caption of captions || []) {
        const word = { text: String(caption.text || '').trim(), start: caption.startMs / 1000, end: caption.endMs / 1000 };
        if (!word.text || !Number.isFinite(word.start) || !Number.isFinite(word.end) || word.start < 0 || word.end < word.start) continue;
        const identity = caption.utteranceId ?? 0;
        let utterance = utterances.at(-1);
        if (!utterance || utterance.identity !== identity) {
            utterance = { identity, words: [] };
            utterances.push(utterance);
        }
        utterance.words.push(word);
    }
    const events = [];
    for (const { words } of utterances) {
        const groups = [];
        let group = [];
        for (const word of words) {
            const candidate = [...group, word];
            if (group.length && (candidate.length > maxWords
                || Array.from(candidate.map(item => item.text).join(' ')).length > 24
                || word.start - group.at(-1).end > .7)) {
                groups.push(group);
                group = [];
            }
            group.push(word);
        }
        if (group.length) groups.push(group);
        for (const phrase of groups) {
            const text = phrase.map(word => word.text).join(' ');
            const texts = phrase.map(word => word.text);
            phrase.forEach((word, index) => {
                if (word.end > word.start) events.push({ start: word.start, end: word.end, text, active: index, words: texts });
                const next = phrase[index + 1];
                if (next && next.start - word.end > 0 && next.start - word.end <= .4) {
                    events.push({ start: word.end, end: next.start, text, active: null, words: texts, revealed: index });
                }
            });
        }
    }
    return events.sort((a, b) => a.start - b.start || a.end - b.end);
}
