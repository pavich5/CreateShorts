import { useState } from 'react';
import { AlertCircle, Loader2, Trash2 } from 'lucide-react';
import Modal from './ui/Modal';
import { apiFetch } from '../lib/api';
import { VIDEO_REMOVED_EVENT } from '../lib/videoRemoval';

export default function RemoveVideoButton({ jobId, clipIndex = 0, fileId, title, disabled, className = 'btn-quiet', onBeforeOpen, onRemoved }) {
    const [open, setOpen] = useState(false);
    const [removing, setRemoving] = useState(false);
    const [error, setError] = useState('');
    const target = fileId ? `file:${fileId}` : jobId;
    const close = () => { if (!removing) setOpen(false); };
    const remove = async () => {
        if (removing || !target) return;
        setRemoving(true);
        setError('');
        try {
            const response = await apiFetch(`/api/clip/${encodeURIComponent(target)}/${fileId ? 0 : clipIndex}`, { method: 'DELETE' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || 'The video could not be removed. Please try again.');
            if (!data.success || !data.removed) throw new Error('Removal was not confirmed. Refresh and try again.');
            setOpen(false);
            window.dispatchEvent(new CustomEvent(VIDEO_REMOVED_EVENT, { detail: data.removed }));
            onRemoved?.(data);
        } catch (err) {
            setError(err.detail || err.message || 'The video could not be removed. Please try again.');
        } finally {
            setRemoving(false);
        }
    };
    return <>
        <button type="button" className={`${className} w-full min-h-[42px] justify-center !bg-red-600 hover:!bg-red-700 !border-red-600 !text-white`} disabled={disabled || !target} onClick={() => { onBeforeOpen?.(); setError(''); setOpen(true); }} title="Permanently remove this video from your computer and CreateShorts"><Trash2 size={16} className="shrink-0" />Remove</button>
        {open && <Modal isOpen title="Remove video?" eyebrow="PERMANENT DELETION" size="sm" onClose={close} hideClose={removing} footer={<div className="flex justify-end gap-2"><button type="button" data-autofocus disabled={removing} className="btn-quiet px-4 py-2.5" onClick={close}>Cancel</button><button type="button" disabled={removing} className="inline-flex items-center justify-center gap-2 rounded-input bg-red-700 hover:bg-red-800 text-white px-4 py-2.5 text-sm disabled:opacity-50" onClick={remove}>{removing ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}{removing ? 'Removing…' : 'Remove permanently'}</button></div>}>
            <p className="text-sm text-ink font-medium break-words mb-3">{title || 'This video'}</p>
            <p className="text-sm text-ink2 leading-relaxed">This permanently deletes this video, its edited versions and saved copies from this computer and CreateShorts.</p>
            <p className="text-sm text-danger mt-3">This cannot be undone.</p>
            {error && <p className="flex items-start gap-2 text-sm text-danger mt-4" role="alert"><AlertCircle size={16} className="shrink-0 mt-0.5" />{error}</p>}
        </Modal>}
    </>;
}
