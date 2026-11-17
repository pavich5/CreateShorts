import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiJson } from '../lib/api';
import ResultCard from './ResultCard';

export default function VideoEditorLoader({ targetId, clipIndex, inputFilename, editorProps = {}, ready = true, onVideoUpdated, onClose }) {
    const [draft, setDraft] = useState(null);
    const [error, setError] = useState('');
    const [attempt, setAttempt] = useState(0);
    useEffect(() => {
        if (!ready) return;
        const controller = new AbortController();
        setDraft(null);
        setError('');
        (async () => {
            const options = { signal: controller.signal };
            if (targetId.startsWith('file:')) {
                const query = inputFilename ? `?input_filename=${encodeURIComponent(inputFilename)}` : '';
                const data = await apiJson(`/api/clip/${encodeURIComponent(targetId)}/0/broll${query}`, options);
                if (controller.signal.aborted) return;
                if (!data.clip || !data.video_url || !data.job_id) throw new Error('This video is unavailable.');
                setDraft({ clip: { ...data.clip, video_url: data.video_url }, jobId: data.job_id, index: 0,
                    fileId: targetId.slice(5) });
            } else {
                let data;
                try {
                    data = await apiJson(`/api/status/${encodeURIComponent(targetId)}`, options);
                } catch (err) {
                    if (err.status !== 404 || controller.signal.aborted || !editorProps.isManaged) throw err;
                    data = await apiJson(`/api/projects/${encodeURIComponent(targetId)}/restore`, { ...options, method: 'POST' });
                }
                if (controller.signal.aborted) return;
                const clip = data.result?.clips?.find((item, index) => (item.index ?? index) === clipIndex);
                if (!clip || clip.deleted || !clip.video_url) throw new Error('This clip is unavailable or has been removed.');
                setDraft({ clip, jobId: targetId, index: clipIndex });
            }
        })().catch(err => { if (!controller.signal.aborted) setError(err.detail || err.message || 'This video could not be opened.'); });
        return () => controller.abort();
    }, [targetId, clipIndex, inputFilename, attempt, ready, editorProps.isManaged]);

    if (draft) return <ResultCard {...editorProps} key={`${draft.jobId}/${draft.index}`} editorOnly initialEditorOpen
        clip={draft.clip} index={draft.index} jobId={draft.jobId} libraryFileId={draft.fileId}
        onVideoUpdated={(index, data) => {
            const updates = { ...(data.clip || {}), ...data };
            const videoUrl = data.new_video_url || data.video_url;
            setDraft(previous => previous && previous.index === index ? { ...previous, clip: {
                ...previous.clip, ...updates, ...(videoUrl ? { video_url: videoUrl } : {}),
            } } : previous);
            onVideoUpdated?.(draft.jobId, index, data);
        }} onEditorClose={onClose} />;

    return <main className="local-studio min-h-screen flex flex-col bg-paper" aria-label="Video editor">
        <header className="h-20 px-5 sm:px-8 flex items-center gap-5 border-b border-rule bg-paper2">
            <h1 className="font-semibold text-lg text-ink">Video editor</h1>
        </header>
        <div className="flex-1 min-h-[400px] flex flex-col items-center justify-center gap-4 p-6 text-center">
            {error ? <><p role="alert" className="text-danger">{error}</p><button type="button" className="btn-quiet" onClick={() => setAttempt(value => value + 1)}>Try again</button></>
                : <p role="status" className="flex items-center gap-3 text-muted"><Loader2 size={20} className="animate-spin" />Opening your editor…</p>}
        </div>
    </main>;
}
