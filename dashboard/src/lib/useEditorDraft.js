import { useCallback, useEffect, useRef, useState } from 'react';

// Tools register their real draft and save action. Playback and focus never
// count as edits, and a successful save explicitly establishes a new baseline.
export function useEditorDraft({ value, ready = true, canSave = false, save, onStateChange }) {
    const serialized = JSON.stringify(value);
    const [baseline, setBaseline] = useState(null);
    const saveRef = useRef(save);
    saveRef.current = save;
    const requestSave = useCallback(async () => (await saveRef.current?.()) === true, []);
    const markSaved = useCallback(nextValue => {
        setBaseline(nextValue === undefined ? serialized : JSON.stringify(nextValue));
    }, [serialized]);
    useEffect(() => {
        if (ready && baseline === null) setBaseline(serialized);
    }, [ready, baseline, serialized]);
    const dirty = ready && baseline !== null && serialized !== baseline;
    useEffect(() => {
        onStateChange?.({ dirty, canSave: Boolean(dirty && canSave), save: requestSave });
    }, [dirty, canSave, requestSave, onStateChange]);
    return { dirty, markSaved };
}
