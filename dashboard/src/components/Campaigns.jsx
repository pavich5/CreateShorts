import Modal from './ui/Modal';
import React, { createContext, useContext, useEffect, useId, useState } from 'react';
import { createPortal } from 'react-dom';
import { Folder, Loader2, Save, Check, Trash2, Pencil } from 'lucide-react';
import { apiJson, apiFetch } from '../lib/api';

const Context = createContext(null);
export function CampaignProvider({ children }) {
  const [data, setData] = useState({ campaigns: [], clips: {} });
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const refresh = async () => {
    setLoading(true); setError('');
    try { setData(await apiJson('/api/local/campaigns')); }
    catch (e) { setError(e.detail || 'Could not load campaigns. Try refreshing.'); }
    finally { setLoading(false); }
  };
  useEffect(() => { refresh(); }, []);
  const mutate = async (path, body, method = 'POST') => {
    setBusy(true);
    try {
      const result = await apiJson('/api/local/campaigns' + path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
      if (path === '') await refresh(); else setData(result);
      return result;
    } finally { setBusy(false); }
  };
  return <Context.Provider value={{ ...data, filter, setFilter, loading, error, busy, refresh, libraryRevision, refreshLibrary: () => setLibraryRevision(v => v+1),
    create: (name) => mutate('', { name }),
    rename: (id, name) => mutate('/' + encodeURIComponent(id), { name }, 'PATCH'),
    remove: async (id) => {
      await mutate('/' + encodeURIComponent(id), undefined, 'DELETE');
      setFilter(current => current === id ? 'unassigned' : current);
    },
    assign: (jobId, clipIndex, campaignId) => mutate('/assign', { job_id: jobId, clip_index: clipIndex, campaign_id: campaignId }),
    assignFile: async (fileId,campaignId) => {
      setBusy(true);
      try { setData(await apiJson('/api/local/library/assign',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file_id:fileId,campaign_id:campaignId})})); }
      finally {setBusy(false);}
    },
  }}>{children}</Context.Provider>;
}
export const useCampaigns = () => useContext(Context);

export function CampaignToolbar({ showFilter = true }) {
  const store = useCampaigns();
  if (!showFilter) return null;
  return <div className="mb-5">
    <label className="flex items-center gap-2 text-sm text-muted"><Folder size={16}/><span>Campaign</span><select aria-label="Filter by campaign" className="input-field !w-auto min-w-[150px]" value={store.filter} onChange={e=>store.setFilter(e.target.value)} disabled={store.loading}><option value="all">All videos</option>{store.campaigns.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
    {store.error && <p role="alert" className="text-sm text-danger mt-2">{store.error} <button onClick={store.refresh} className="underline">Retry</button></p>}
  </div>;
}

export function CampaignPicker({ jobId, clipIndex = null, fileId = null }) {
  const store = useCampaigns();
  const [error, setError] = useState('');
  const key = fileId ? 'file/'+fileId : jobId + (clipIndex == null ? '/project' : '/clip/' + clipIndex);
  const id = store.clips[key] || '';
  const update = async (value) => {
    setError('');
    try { if(fileId) await store.assignFile(fileId,value || null); else await store.assign(jobId, clipIndex, value || null); }
    catch (e) { setError(e.detail || 'Could not update campaign. Please try again.'); }
  };
  const kind = clipIndex == null ? 'video' : 'short';
  return <div className="mt-3 space-y-2" data-campaign-picker>
    <label className="block text-xs font-medium text-muted">{fileId ? 'Campaign' : clipIndex == null ? 'Video & shorts campaign' : 'Short campaign'}
      <select aria-label={`${clipIndex == null ? 'Video' : 'Short'} campaign`} className="input-field mt-1.5 !text-sm !py-2" value={id} onChange={e => update(e.target.value)} disabled={store.busy || store.loading || !!store.error}><option value="">Unassigned</option>{store.campaigns.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
    </label>

    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </div>;
}

export function CampaignNav({ onOpen, active = false }) {
  const store = useCampaigns();
  const [tooltip, setTooltip] = useState(null);
  const tooltipId = useId();
  const showName = (event, campaign) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setTooltip({ id: campaign.id, name: campaign.name,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - 328)),
      top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 100)) });
  };
  useEffect(() => {
    const hide = () => setTooltip(null);
    window.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);
    return () => {
      window.removeEventListener('scroll', hide, true);
      window.removeEventListener('resize', hide);
    };
  }, []);
  const [deleting, setDeleting] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const [name, setName] = useState('');
  const [renameError, setRenameError] = useState('');
  const [error, setError] = useState('');
  const entries = [{id:'all',name:'All videos'}, {id:'unassigned',name:'Unassigned'}, ...store.campaigns];
  const close = () => { if (!store.busy) { setDeleting(null); setError(''); } };
  const remove = async () => {
    setError('');
    try { await store.remove(deleting.id); setDeleting(null); }
    catch (e) { setError(e.detail || 'Could not delete this category. Please try again.'); }
  };
  const closeRename = () => { if (!store.busy) { setRenaming(null); setRenameError(''); } };
  const rename = async (event) => {
    event.preventDefault();
    setRenameError('');
    try { await store.rename(renaming.id, name); setRenaming(null); }
    catch (e) { setRenameError(e.detail || 'Could not rename this campaign. Please try again.'); }
  };
  return <div className="px-4 pb-4"><p className="px-3 text-xs font-medium text-slate-400 mb-2">Categories</p>
    {entries.map(c => <div key={c.id} className="flex items-center gap-1">
      <button type="button" aria-describedby={tooltip?.id === c.id ? tooltipId : undefined} onMouseEnter={event => showName(event, c)} onMouseLeave={() => setTooltip(null)} onFocus={event => showName(event, c)} onBlur={() => setTooltip(null)} aria-current={active && store.filter === c.id ? 'page' : undefined} onClick={() => {setTooltip(null);store.setFilter(c.id);onOpen();}} className={`local-nav-item flex-1 min-w-0 text-left text-sm ${active && store.filter === c.id ? 'is-active' : ''}`}><Folder size={16} className="shrink-0"/><span className="truncate">{c.name}</span></button>
      {!['all', 'unassigned'].includes(c.id) && <button type="button" aria-label={`Delete category ${c.name}`} title={`Delete category ${c.name}`} disabled={store.busy} onClick={() => {setError('');setDeleting(c);}} className="p-2.5 rounded-lg text-slate-400 hover:text-red-300 hover:bg-red-500/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-300 shrink-0"><Trash2 size={15}/></button>}
      {!['all', 'unassigned'].includes(c.id) && <button type="button" aria-label={`Rename campaign ${c.name}`} title={`Rename campaign ${c.name}`} disabled={store.busy} onClick={() => {setRenameError('');setName(c.name);setRenaming(c);}} className="p-2.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-white shrink-0"><Pencil size={15}/></button>}
    </div>)}
    {tooltip && createPortal(<div id={tooltipId} role="tooltip" className="fixed z-[200] pointer-events-none rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-white shadow-lg break-words" style={{ left: tooltip.left, top: tooltip.top, maxWidth: 'min(320px, calc(100vw - 16px))' }}>{tooltip.name}</div>, document.body)}
    <Modal isOpen={!!renaming} onClose={store.busy ? undefined : closeRename} title="Rename campaign" size="sm" footer={<div className="flex justify-end gap-3"><button type="button" onClick={closeRename} disabled={store.busy} className="btn-quiet">Cancel</button><button type="submit" form="rename-campaign" disabled={store.busy || !name.trim()} className="btn-primary">{store.busy && <Loader2 size={16} className="animate-spin"/>}{store.busy ? 'Saving…' : 'Save name'}</button></div>}>
      <form id="rename-campaign" onSubmit={rename}>
        <label className="block text-sm font-medium text-ink">Campaign name<input autoFocus required maxLength={80} value={name} onChange={e => setName(e.target.value)} disabled={store.busy} className="input-field mt-2"/></label>
        {renameError && <p role="alert" className="mt-3 text-sm text-danger">{renameError}</p>}
      </form>
    </Modal>
    <Modal isOpen={!!deleting} onClose={store.busy ? undefined : close} title="Delete category?" size="sm" footer={<div className="flex justify-end gap-3"><button type="button" onClick={close} disabled={store.busy} className="btn-quiet">Cancel</button><button type="button" onClick={remove} disabled={store.busy} className="btn-primary !bg-red-600 !border-red-600 !text-white">{store.busy ? <Loader2 size={16} className="animate-spin"/> : <Trash2 size={16}/>} {store.busy ? 'Deleting…' : 'Delete category'}</button></div>}>
      <p className="text-sm text-ink">Delete <strong>{deleting?.name}</strong>? Its videos and shorts will stay in your library and become Unassigned.</p>
      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
    </Modal>
  </div>;
}

export function SaveToClips({ sourceUrl, sourceKey, filename, campaignId = null, alreadySaved = false }) {
  const store=useCampaigns();
  const [editing,setEditing]=useState(false);
  const [name,setName]=useState((filename || 'My short').replace(/\.mp4$/i, ''));
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [savedPath,setSavedPath]=useState('');
  const saved=alreadySaved || !!store.saved_sources?.[sourceKey] || !!savedPath;
  const save=async(e)=>{
    e.preventDefault();setBusy(true);setError('');
    try {
      const response=await fetch(sourceUrl);
      if(!response.ok)throw new Error('The video could not be read. Try reopening it.');
      const blob=await response.blob();
      const query=new URLSearchParams({name,source_key:sourceKey});
      if(campaignId)query.set('campaign_id',campaignId);
      const result=await apiFetch('/api/local/library/save?'+query,{method:'POST',headers:{'Content-Type':'video/mp4'},body:blob});
      if(!result.ok){const body=await result.json();throw new Error(body.detail || 'Could not save video.');}
      const data=await result.json();setSavedPath(data.saved_path);setEditing(false);
      await store.refresh();store.refreshLibrary();
    } catch(e){setError(e.message || 'Could not save this video.');}
    finally{setBusy(false);}
  };
  const close=()=>{if(!busy){setEditing(false);setError('');}};
  return <div>
    <button type="button" onClick={()=>{setError('');setEditing(true);}} className={`w-full btn-primary py-2.5 text-sm ${saved ? '!bg-emerald-50 !text-emerald-700 !border-emerald-200' : ''}`} disabled={busy}>{saved ? <Check size={16}/> : <Save size={16}/>} {saved ? 'Save another version' : 'Save to Clips'}</button>
    <Modal isOpen={editing} onClose={busy ? undefined : close} title="Save your video" eyebrow="SAVE TO CLIPS" size="md" footer={<div className="flex justify-end gap-3"><button type="button" onClick={close} disabled={busy} className="btn-quiet px-4 py-2.5">Cancel</button><button type="submit" form={'save-clip-'+sourceKey} disabled={busy || !name.trim()} className="btn-primary px-5 py-2.5">{busy ? <Loader2 size={16} className="animate-spin"/> : <Save size={16}/>} {busy ? 'Saving…' : 'Save video'}</button></div>}>
      <form id={'save-clip-'+sourceKey} onSubmit={save} className="space-y-5">
        <p className="text-sm text-muted leading-relaxed">Give this version a name so you can find it in your campaign.</p>
        <label className="block text-sm font-medium text-ink">Video name<input autoFocus aria-label="Video name" value={name} onChange={e=>setName(e.target.value)} required maxLength={180} disabled={busy} className="input-field mt-2"/><span className="block text-xs font-normal text-muted mt-2">The .mp4 extension is added automatically.</span></label>
        <div className="rounded-xl border border-rule bg-paper p-4 flex gap-3"><Folder size={20} className="text-brass shrink-0 mt-0.5"/><div className="min-w-0"><p className="text-sm font-medium text-ink">{store.campaigns.find(c=>c.id===campaignId)?.name || 'Unassigned'} <span className="text-muted font-normal">/ Saved</span></p><p className="text-xs text-muted mt-1 break-all">Documents/Clips</p></div></div>
        {error && <p role="alert" className="text-sm text-danger rounded-xl bg-danger/5 p-3">{error}</p>}
      </form>
    </Modal>
    {savedPath && <p role="status" className="mt-2 text-xs text-ok">Saved to your campaign.</p>}
  </div>;
}
