import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Search, RefreshCw, Film, FolderOpen, ArrowUpRight, Scissors, Loader2, Play, AlertCircle } from 'lucide-react';
import ResultCard from './ResultCard';
import { openEditorRoute } from '../lib/editorRoute';
import { uniqueLibraryVideos } from '../lib/libraryVideos';
import RemoveVideoButton from './RemoveVideoButton';
import { apiJson } from '../lib/api';
import { getApiUrl } from '../config';
import { SaveToClips, useCampaigns } from './Campaigns';
import { isRemovedVideo, VIDEO_REMOVED_EVENT } from '../lib/videoRemoval';

const projectDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
};
const durationLabel = (value) => {
  if (!Number.isFinite(Number(value)) || value == null) return '';
  const seconds = Math.max(0, Math.round(Number(value)));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

function ProjectPreview({ project }) {
  const [failed, setFailed] = useState(false);
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef(null);
  return (
    <div className="local-clip-stage flex items-center justify-center rounded-t-[16px]">
      {failed ? (
        <div className="px-6 text-center text-slate-300" role="status">
          <Film className="mx-auto mb-3" size={28} />
          <p className="text-sm">Preview unavailable</p>
          <p className="text-xs mt-1 text-slate-400">Open the project to check its clips.</p>
        </div>
      ) : (
        <>
        <video
          ref={videoRef}
          src={getApiUrl(project.video_url)}
          playsInline preload="metadata"
          aria-label={`Preview ${project.title}`}
          className="block w-full h-full object-contain"
          onError={() => setFailed(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onPlay={(event) => {
            setPlaying(true);
            const current = event.currentTarget;
            current.ownerDocument.querySelectorAll('video').forEach((video) => {
              if (video !== current && !video.paused) video.pause();
            });
          }}
        />
        <button
          type="button"
          className="local-clip-toggle"
          aria-label={`${playing ? 'Pause' : 'Play'} project preview: ${project.title}`}
          onClick={() => {
            const video = videoRef.current;
            if (!video) return;
            if (video.paused) video.play().catch(() => setPlaying(false));
            else video.pause();
          }}
        />
        </>
      )}
      <span className="absolute top-3 left-3 pointer-events-none rounded-md bg-slate-950/80 px-2.5 py-1 text-xs font-medium text-white">
        {project.clip_count} {project.clip_count === 1 ? 'short' : 'shorts'}
      </span>
    </div>
  );
}

export default function LocalLibrary({ onOpenProject, onEdit, editorProps = {}, compact = false, refreshKey }) {
  const Heading = compact ? 'h2' : 'h1';
  const campaignStore = useCampaigns();
  const [projects, setProjects] = useState(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [bucket,setBucket]=useState('all');
  const [limit,setLimit]=useState(24);
  const [revision, setRevision] = useState(0);
  const [opening, setOpening] = useState(null);
  const [openError, setOpenError] = useState('');
  const [aiVersions] = useState({});

  const updateClipVideo = (project, data) => {
    const timeline = Object.fromEntries(['duration_seconds', 'start', 'end', 'recipe', 'layout_ranges', 'caption_words', 'source_edits', 'source_visuals', 'auto_hook']
      .filter(key => data[key] !== undefined).map(key => [key, data[key]]));
    setProjects(previous => previous.map(item => {
      const matches = project.local_file_id ? item.local_file_id === project.local_file_id
        : item.job_id === project.job_id && item.clip_index === project.clip_index;
      if (!matches) return item;
      const currentClip = item.clip || project.clip;
      return { ...item, video_url: data.new_video_url, clip: {
        ...currentClip, ...timeline,
        _aiEditCardKey: currentClip._aiEditCardKey ?? `${currentClip.start}-${currentClip.end}-${JSON.stringify(currentClip.recipe || null)}`,
        video_url: data.new_video_url, ai_edit: {
          ...currentClip.ai_edit, preview_start: data.preview_start, preview_end: data.preview_end,
        },
      } };
    }));
  };

  useEffect(() => {
    let cancelled = false;
    setError('');
    Promise.all([apiJson('/api/local/projects'),apiJson('/api/local/library')])
      .then(([data,library]) => { if (!cancelled) setProjects(compact ? (data.projects || []) : [...(library.videos || []), ...(data.projects || []).flatMap(p => (p.clips || []).map((clip,index) => ({...p,clip,clip_index:clip.index ?? index, title:clip.video_title_for_youtube_short || clip.title || p.title, kind:'generated'})).filter(project => !project.clip.deleted))]); })
      .catch(() => { if (!cancelled) setError('Could not load your local projects. Check that CreateShorts is running and try again.'); });
    return () => { cancelled = true; };
  }, [refreshKey, revision, campaignStore.libraryRevision, compact]);

  useEffect(() => {
    const onRemoved = event => {
      const removed = event.detail;
      setProjects(previous => previous?.filter(project => !isRemovedVideo(removed, { jobId: project.job_id, clipIndex: project.clip_index, fileId: project.local_file_id, sourceKey: project.source_key })) ?? previous);
      setRevision(value => value + 1);
    };
    window.addEventListener(VIDEO_REMOVED_EVENT, onRemoved);
    return () => window.removeEventListener(VIDEO_REMOVED_EVENT, onRemoved);
  }, []);

  const matchingProjects = useMemo(() => {
    return (projects || []).map(project => {
      if (!project.source_key) return project;
      const source = projects.find(p => p.clip && p.job_id+'/clip/'+p.clip_index === project.source_key);
      return source ? {...project, job_id:source.job_id, clip_index:source.clip_index, clip:{...source.clip,...project.clip,video_url:project.video_url}} : project;
    }).filter((project) => {
      if(compact && project.local_file_id) return false;
      if (!String(project.title || '').toLowerCase().includes(query.trim().toLowerCase())) return false;
      if (compact || campaignStore.filter === 'all') return true;
      const memberships = project.clip && !project.local_file_id ? [campaignStore.clips[project.job_id+'/clip/'+project.clip_index]].filter(Boolean) : project.local_file_id ? [campaignStore.clips['file/'+project.local_file_id]].filter(Boolean) : Object.entries(campaignStore.clips).filter(([key]) => key.startsWith(project.job_id + '/')).map(([,id]) => id);
      return campaignStore.filter === 'unassigned' ? memberships.length === 0 : memberships.includes(campaignStore.filter);
    });
  }, [projects, query, compact, campaignStore.filter, campaignStore.clips]);
  const allVideos = useMemo(() => compact ? matchingProjects : uniqueLibraryVideos(matchingProjects), [matchingProjects, compact]);
  const collectionCounts = useMemo(() => ({
    all: allVideos.length,
    generated: matchingProjects.filter(project => project.kind === 'generated').length,
    saved: matchingProjects.filter(project => project.kind === 'saved').length,
  }), [matchingProjects, allVideos]);
  const visible = useMemo(() => {
    if (compact) return matchingProjects.slice(0, 3);
    const collection = bucket === 'all' ? allVideos : matchingProjects.filter(project => project.kind === bucket);
    // Rank the displayed rows after saved copies inherit their source score.
    // Keep clip_index intact so editing still targets the original clip.
    return [...collection].sort((a, b) => {
      const aScore = Number.isFinite(a.clip?.predicted_score) ? a.clip.predicted_score : -1;
      const bScore = Number.isFinite(b.clip?.predicted_score) ? b.clip.predicted_score : -1;
      return bScore - aScore;
    });
  }, [matchingProjects, allVideos, compact, bucket]);
  useEffect(()=>setLimit(24),[query,bucket,campaignStore.filter]);

  const openProject = async (jobId) => {
    if (opening) return;
    setOpening(jobId);
    setOpenError('');
    try {
      await onOpenProject?.(jobId);
    } catch {
      setOpenError('This project could not be opened. Its files may have expired. Refresh the library and try again.');
    } finally {
      setOpening(null);
    }
  };

  const refreshButton = (
    <button type="button" onClick={() => setRevision((value) => value + 1)} className="btn-quiet px-3 py-2" aria-label="Refresh local projects" title="Refresh local projects">
      <RefreshCw size={15} />{!compact && <span>Refresh</span>}
    </button>
  );

  return (
    <section className={compact ? 'local-session-panel card p-5' : 'local-workspace h-full overflow-y-auto custom-scrollbar p-4 sm:p-7'} aria-label={compact ? 'Recent local projects' : 'Generated shorts library'}>
      <div className={compact ? 'flex items-center justify-between gap-3 mb-4' : 'local-page-heading flex flex-wrap items-start justify-between gap-4 mb-7'}>
        <div>
          <Heading className={`${compact ? 'text-base' : 'text-2xl sm:text-3xl'} font-semibold text-ink tracking-tight`}>{compact ? 'Recent projects' : campaignStore.filter === 'all' ? 'All videos' : campaignStore.campaigns.find(c=>c.id===campaignStore.filter)?.name || 'Videos'}</Heading>
          <p className="text-sm text-muted mt-1 leading-relaxed">{compact ? 'Pick up where you left off.' : 'All videos from Documents/Clips, plus your CreateShorts projects. Highest scores first.'}</p>
        </div>
        {refreshButton}
      </div>

      {!compact && <><div role="group" aria-label="Video collection" className="flex gap-2 mb-5">{[['all','All videos'],['generated','Generated'],['saved','Saved']].map(([id,label])=><button key={id} type="button" aria-pressed={bucket===id} onClick={()=>setBucket(id)} className={bucket===id ? 'btn-primary px-4 py-2' : 'btn-quiet px-4 py-2'}>{label} <span className="opacity-70">{collectionCounts[id]}</span></button>)}</div></>}


      {error ? (
        <div className="rounded-xl border border-danger/30 bg-danger/5 p-4 text-sm" role="alert">
          <div className="flex gap-2 items-start"><AlertCircle size={18} className="text-danger shrink-0 mt-0.5" /><p className="text-ink2">{error}</p></div>
          <button type="button" onClick={() => setRevision((value) => value + 1)} className="btn-ghost px-3 py-2 mt-3">Try again</button>
        </div>
      ) : projects === null ? (
        <div className="flex items-center justify-center gap-2 py-12 text-muted" role="status"><Loader2 size={19} className="animate-spin" /><span className="text-sm">Loading projects…</span></div>
      ) : visible.length === 0 ? (
        <div className={`local-empty-state text-center ${compact ? 'py-8' : 'card px-6 py-16'}`}>
          <div className="w-12 h-12 rounded-xl bg-paper3 flex items-center justify-center mx-auto mb-4"><Film size={23} className="text-muted" /></div>
          <h2 className="font-medium text-ink">{query ? 'No matching projects' : campaignStore.filter !== 'all' ? 'No videos in this campaign' : 'Your shorts will appear here'}</h2>
          <p className="text-sm text-muted leading-relaxed mt-2 max-w-sm mx-auto">{query ? 'Try a different project title.' : campaignStore.filter !== 'all' ? 'Create clips using this campaign name to add videos here.' : 'Generate clips from your first video, then return here to preview and edit them.'}</p>
          {query && <button type="button" onClick={() => setQuery('')} className="btn-quiet mt-4">Clear search</button>}
        </div>
      ) : compact ? (
        <div className="space-y-2">
          {visible.map((project) => (
            <button key={project.job_id} type="button" onClick={() => openProject(project.job_id)} disabled={!!opening} className="w-full text-left flex items-center gap-3 p-3 rounded-xl border border-rule hover:border-brass/40 hover:bg-paper3 transition-colors disabled:opacity-60">
              <span className="w-10 h-12 rounded-lg bg-slate-900 text-white flex items-center justify-center shrink-0"><Play size={17} /></span>
              <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-ink truncate">{project.title}</span><span className="block text-xs text-muted mt-1">{project.clip_count} {project.clip_count === 1 ? 'short' : 'shorts'} · {projectDate(project.created_at)}</span></span>
              {opening === project.job_id ? <Loader2 size={16} className="animate-spin text-brass shrink-0" /> : <ArrowUpRight size={17} className="text-muted shrink-0" />}
            </button>
          ))}
        </div>
      ) : (
        <div className="local-results-grid">
          {visible.slice(0,limit).map((project) => project.clip ? (
            <ResultCard key={project.local_file_id || project.job_id+'-'+project.clip_index+'-'+(project.clip._aiEditCardKey ?? `${project.clip.start}-${project.clip.end}-${JSON.stringify(project.clip.recipe || null)}`)} clip={project.clip} index={project.clip_index} jobId={project.job_id} libraryFileId={project.local_file_id} savedVersion={project.saved} {...editorProps} onVideoUpdated={(_index, data) => updateClipVideo(project, data)} onEditClip={() => onEdit?.(project,'edit')} onReframeClip={() => onEdit?.(project,'reframe')} />
          ) : (
            <article key={project.job_id} className="local-clip-card card overflow-hidden" aria-label={project.title}>
              <ProjectPreview key={aiVersions[project.local_file_id] || project.video_url} project={{...project, video_url:aiVersions[project.local_file_id] || project.video_url}} />
              <div className="p-5">
                <p className="text-xs text-muted mb-2">{projectDate(project.created_at)}{durationLabel(project.duration_seconds) ? ` · ${durationLabel(project.duration_seconds)} preview` : ''}</p>
                <h2 className="text-base font-semibold text-ink leading-snug line-clamp-2 min-h-[2.75rem]" title={project.title}>{project.title}</h2>
                <div className="text-xs font-medium text-muted mb-2">{project.kind === 'saved' ? 'Saved version' : project.local_file_id ? 'Generated video' : 'CreateShorts project'}</div>
                {project.local_file_id && <SaveToClips sourceUrl={getApiUrl(aiVersions[project.local_file_id] || project.video_url)} sourceKey={'file/'+project.local_file_id} filename={project.filename} campaignId={campaignStore.clips['file/'+project.local_file_id]} alreadySaved={project.saved} />}
                {project.local_file_id && <button type="button" onClick={() => openEditorRoute(`file:${project.local_file_id}`, 0, (aiVersions[project.local_file_id] || project.video_url).split('/').pop())} className="btn-primary w-full mt-2"><Scissors size={16}/>Edit</button>}
                {project.local_file_id && <div className="mt-2"><RemoveVideoButton fileId={project.local_file_id} title={project.title} /></div>}
                {!project.local_file_id && <button type="button" onClick={() => openProject(project.job_id)} disabled={!!opening} className="btn-primary w-full mt-4 py-2.5">
                  {opening === project.job_id ? <Loader2 size={16} className="animate-spin" /> : <FolderOpen size={16} />}Open project
                </button>}
              </div>
            </article>
          ))}
        </div>
      )}
      {!compact && visible.length > limit && <button className="btn-ghost mt-6" onClick={()=>setLimit(v=>v+24)}>Show more videos ({visible.length-limit} remaining)</button>}
      {openError && <p className="mt-4 text-sm text-danger" role="alert">{openError}</p>}
    </section>
  );
}
