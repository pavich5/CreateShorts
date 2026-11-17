import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, Check, Film, Loader2 } from 'lucide-react';
import Modal from './ui/Modal';
import './unified-editor.css';

export default function VideoEditorPage({ title, sections, busy = false, error, notice, onClose }) {
    const [selected, setSelected] = useState(sections[0]?.id);
    const content = useRef(null);
    const page = useRef(null);
    const [draft, setDraft] = useState({ dirty: false, canSave: false });
    const saveAction = useRef(null);
    const [pending, setPending] = useState(null);
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState('');
    const isDirty = draft.dirty;
    const locked = busy || saving;
    const registerDraft = useCallback(state => {
        saveAction.current = state.save;
        setDraft(previous => previous.dirty === state.dirty && previous.canSave === state.canSave
            ? previous : { dirty: state.dirty, canSave: state.canSave });
    }, []);
    useEffect(() => {
        const previousTitle = document.title;
        const previousOverflow = document.body.style.overflow;
        document.title = `${title || 'Video'} · CreateShorts Editor`;
        document.body.style.overflow = 'hidden';
        page.current?.focus();
        return () => { document.title = previousTitle; document.body.style.overflow = previousOverflow; };
    }, [title]);
    useEffect(() => {
        if (!isDirty) return;
        const beforeUnload = event => { event.preventDefault(); event.returnValue = ''; };
        window.addEventListener('beforeunload', beforeUnload);
        return () => window.removeEventListener('beforeunload', beforeUnload);
    }, [isDirty]);
    const active = sections.find(section => section.id === selected) || sections[0];
    const navigate = target => {
        content.current?.querySelectorAll('video, audio').forEach(media => media.pause());
        setPending(null); setSaveError('');
        if (target === 'home') {
            window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#app`);
            window.dispatchEvent(new Event('hashchange'));
        } else if (target === 'close') onClose();
        else {
            saveAction.current = null;
            setDraft({ dirty: false, canSave: false });
            setSelected(target);
            if (content.current) content.current.scrollTop = 0;
        }
    };
    const close = () => { if (!locked) { if (isDirty) { setSaveError(''); setPending('close'); } else navigate('close'); } };
    const goHome = () => {
        if (locked) return;
        if (isDirty) { setSaveError(''); setPending('home'); }
        else navigate('home');
    };
    const select = id => {
        if (locked || id === selected) return;
        if (isDirty) { setSaveError(''); setPending(id); return; }
        navigate(id);
    };
    const saveAndNavigate = async () => {
        if (locked || !draft.canSave || !saveAction.current) return;
        const destination = pending;
        setSaving(true); setSaveError('');
        try {
            if (await saveAction.current() === true) navigate(destination);
            else setSaveError('Your changes could not be saved. Check the fields and try again.');
        } catch (err) {
            setSaveError(err.message || 'Your changes could not be saved. Please try again.');
        } finally {
            setSaving(false);
        }
    };
    return createPortal(<main ref={page} tabIndex={-1} aria-label="Video editor" className="unified-editor-page">
        <header className="unified-editor-page-header">
            <div className="unified-editor-page-title"><button type="button" className="unified-editor-home" aria-label="Go to home page" title="Home" disabled={locked} onClick={goHome}><Film size={18}/></button><div><p>VIDEO EDITOR</p><h1>{title || 'Edit video'}</h1></div></div>
            <span className="unified-editor-page-state">{locked ? 'Saving changes…' : isDirty ? 'Unsaved changes' : 'Ready to edit'}</span>
        </header>
        <div className="unified-editor-workspace">
            <aside className="unified-editor-sidebar">
                <span className="unified-editor-nav-label">Editing tools</span>
                <div className="unified-editor-tabs" role="tablist" aria-label="Video editing tools" aria-orientation="vertical" onKeyDown={event => {
                    if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || locked) return;
                    event.preventDefault();
                    const current = sections.findIndex(section => section.id === active.id);
                    const next = event.key === 'Home' ? 0 : event.key === 'End' ? sections.length - 1 : (current + (['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1) + sections.length) % sections.length;
                    select(sections[next].id);
                    event.currentTarget.querySelectorAll('[role="tab"]')[next]?.focus();
                }}>
                    {sections.map(({ id, label, hint, icon }) => { const Icon = icon; return <button key={id} id={`edit-tab-${id}`} type="button" role="tab" aria-selected={active.id === id} aria-controls={`edit-panel-${id}`} tabIndex={active.id === id ? 0 : -1} disabled={locked} onClick={() => select(id)}><Icon size={18} /><span><strong>{label}</strong><small>{hint}</small></span></button>; })}
                </div>
                <p className="unified-editor-sidebar-help">Save a section to apply it, then keep editing here.</p>
                <button type="button" className="btn-quiet unified-editor-done" disabled={locked} onClick={close}>Done editing</button>
            </aside>
            <div className="unified-editor-main" ref={content}>
                <div className="unified-editor-section-heading"><div><h3>{active?.label}</h3><p>{active?.description}</p></div>{busy ? <span role="status"><Loader2 size={14} className="animate-spin" />Saving…</span> : notice ? <span role="status"><Check size={14} />{notice}</span> : null}</div>
                {error && <div className="unified-editor-error" role="alert"><AlertCircle size={16} /><span>{error}</span></div>}
                {active && <div key={active.id} id={`edit-panel-${active.id}`} role="tabpanel" aria-labelledby={`edit-tab-${active.id}`} className={`unified-editor-panel unified-editor-panel-${active.id}`}>{active.render({ active: true, onClose: close, onEditorStateChange: registerDraft })}</div>}
            </div>
        </div>
        <Modal isOpen={pending !== null} onClose={locked ? undefined : () => setPending(null)} hideClose={locked} title="Save your changes?" eyebrow={active?.label} className="editor-unsaved-modal" footer={<div className="editor-unsaved-actions">
            <button type="button" className="btn-quiet" disabled={locked} onClick={() => setPending(null)}>Cancel</button>
            <button type="button" className="btn-quiet" disabled={locked} onClick={() => navigate(pending)}>Discard changes</button>
            <button type="button" className="btn-primary" disabled={locked || !draft.canSave} onClick={saveAndNavigate}>{locked && <Loader2 size={15} className="animate-spin" />}{locked ? 'Saving…' : 'Save changes'}</button>
        </div>}>
            <p className="text-sm text-ink2 leading-relaxed">Save the changes in {active?.label} before {['close', 'home'].includes(pending) ? 'leaving the editor' : 'switching tools'}?</p>
            {!draft.canSave && !locked && <p className="text-xs text-muted mt-3">Finish the highlighted fields before saving, or discard these changes.</p>}
            {saveError && <p className="text-sm text-danger mt-3" role="alert">{saveError}</p>}
        </Modal>
    </main>, document.body);
}
