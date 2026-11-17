import { processingMessage } from './lib/processingStatus';
import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Upload, Sparkles, Youtube, Instagram, Share2, ChevronDown, Check, Activity, LayoutDashboard, Settings, Plus, History, X, Terminal, Shield, LayoutGrid, Image, Globe, RotateCcw, Calendar, AlertTriangle, KeyRound, Bot, Users, Smartphone, ExternalLink, Copy, CheckCircle2, Mail, Loader2, Download, Menu, Lock, Rocket } from 'lucide-react';
import MediaInput from './components/MediaInput';
import ResultCard from './components/ResultCard';
import VideoEditorLoader from './components/VideoEditorLoader';
import LocalLibrary from './components/LocalLibrary';
import { CampaignNav, useCampaigns } from './components/Campaigns';
import ProcessingAnimation from './components/ProcessingAnimation';
// import Gallery from './components/Gallery';
import ClipEditor from './components/ClipEditor';
import ReframeEditor from './components/ReframeEditor';
import UsageMeter from './components/UsageMeter';
import WatermarkModal, { watermarkNoticeDismissed } from './components/WatermarkModal';
import { getApiUrl } from './config';
import TrialGate from './components/TrialGate';
import AdvancedBanner from './components/AdvancedBanner';
import ProfileMenu from './components/ProfileMenu';
import Modal from './components/ui/Modal';
import { useAuth } from './contexts/AuthContext';
import { apiFetch, apiJson, QuotaError, QUOTA_WALL_EVENT } from './lib/api';
import { track } from './lib/analytics';
import { readConnectReturn, stripConnectReturn } from './lib/connectReturn';
import { rememberTabAfterLogin } from './lib/tabAfterLogin';
import { JobPollError, pollFailureAction } from './lib/jobPoll';
import { isRemovedVideo, VIDEO_REMOVED_EVENT } from './lib/videoRemoval';
import { closeEditorRoute, parseEditorRoute } from './lib/editorRoute';

// Enhanced "Encryption" using XOR + Base64 with a Salt
// This is better than plain Base64 but still client-side.
const SECRET_KEY = import.meta.env.VITE_ENCRYPTION_KEY || "OpenShorts-Static-Salt-Change-Me";
const ENCRYPTION_PREFIX = "ENC:";

const encrypt = (text) => {
  if (!text) return '';
  try {
    const xor = text.split('').map((c, i) =>
      String.fromCharCode(c.charCodeAt(0) ^ SECRET_KEY.charCodeAt(i % SECRET_KEY.length))
    ).join('');
    return ENCRYPTION_PREFIX + btoa(xor);
  } catch (e) {
    console.error("Encryption failed", e);
    return text;
  }
};

const decrypt = (text) => {
  if (!text) return '';
  if (text.startsWith(ENCRYPTION_PREFIX)) {
    try {
      const raw = text.slice(ENCRYPTION_PREFIX.length);
      // Check if it's plain base64 or our custom XOR (simple try)
      const xor = atob(raw);
      const result = xor.split('').map((c, i) =>
        String.fromCharCode(c.charCodeAt(0) ^ SECRET_KEY.charCodeAt(i % SECRET_KEY.length))
      ).join('');
      return result;
    } catch (e) {
      // Fallback if decryption fails (might be old plain text)
      return '';
    }
  }
  // Backward compatibility: If no prefix, assume old plain text (or return empty if you want to force re-login)
  // For migration: Return text as is, so it populates the field, and next save will encrypt it.
  return text;
};

// Simple TikTok icon sine Lucide might not have it or it varies
const TikTokIcon = ({ size = 16, className = "" }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M19.589 6.686a4.793 4.793 0 0 1-3.77-4.245V2h-3.445v13.672a2.896 2.896 0 0 1-5.201 1.743l-.002-.001.002.001a2.895 2.895 0 0 1 3.183-4.51v-3.5a6.329 6.329 0 0 0-5.394 10.692 6.33 6.33 0 0 0 10.857-4.424V8.687a8.182 8.182 0 0 0 4.773 1.526V6.79a4.831 4.831 0 0 1-1.003-.104z" />
  </svg>
);

// Cloud accounts get an auto-generated opaque id (os_<hash>) as username —
// meaningless to the user, so the selector shows connected networks instead.
const isAutoProfileId = (username) => /^os_[0-9a-f]/i.test(username || "");

/* The job a signed-out visitor started, parked until they come back signed in.
 *
 * Pressing "get free clips" without a session used to open the login modal and
 * drop the work on the floor: the magic link lands on a fresh document, so the
 * pasted URL was gone and the highest-intent step in the funnel ended in an
 * empty form. The same is true of the Google round trip.
 *
 * So the request is written down before the redirect and replayed after it.
 * localStorage rather than sessionStorage because the magic link usually opens
 * in a new tab, and a TTL because a request parked last week is not what the
 * user is doing now. File uploads cannot be serialised (a File is not JSON), so
 * those are only resumed within the same document, via the in-memory fallback. */
const PENDING_JOB_KEY = 'os_pending_job';
const PENDING_JOB_TTL_MS = 60 * 60 * 1000;
let pendingJobInMemory = null;

function stashPendingJob(data) {
  const stamp = Date.now();
  const entry = { stamp, data: { ...data, payload: typeof data?.payload === 'string' ? data.payload : null } };
  pendingJobInMemory = { stamp, data };
  try {
    localStorage.setItem(PENDING_JOB_KEY, JSON.stringify(entry));
  } catch (_) { /* private mode: the in-memory copy still covers same-document flows */ }
  return stamp;
}

function peekPendingJob() {
  try {
    const raw = localStorage.getItem(PENDING_JOB_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed?.data?.payload && Date.now() - (parsed.stamp || 0) < PENDING_JOB_TTL_MS) {
        return parsed;
      }
      localStorage.removeItem(PENDING_JOB_KEY);
    }
  } catch (_) { /* ignore */ }
  // A File cannot survive a reload; this covers a sign-in that did not reload.
  if (pendingJobInMemory && Date.now() - pendingJobInMemory.stamp < PENDING_JOB_TTL_MS) {
    return pendingJobInMemory;
  }
  return null;
}

function clearPendingJob() {
  pendingJobInMemory = null;
  try {
    localStorage.removeItem(PENDING_JOB_KEY);
  } catch (_) { /* ignore */ }
}

const formatRetention = (seconds) => {
  if (seconds >= 86400) return `${Math.round(seconds / 86400)} day${seconds >= 172800 ? 's' : ''}`;
  if (seconds >= 3600) return `${Math.round(seconds / 3600)} hour${seconds >= 7200 ? 's' : ''}`;
  return `${Math.max(1, Math.round(seconds / 60))} min`;
};

const ProfileNetworkIcons = ({ profile, size = 12 }) => (
  <span className="flex items-center gap-1.5">
    <span className={profile?.connected?.includes('tiktok') ? 'text-ink' : 'text-muted opacity-40'}>
      <TikTokIcon size={size} />
    </span>
    <span className={profile?.connected?.includes('instagram') ? 'text-ink' : 'text-muted opacity-40'}>
      <Instagram size={size} />
    </span>
    <span className={profile?.connected?.includes('youtube') ? 'text-ink' : 'text-muted opacity-40'}>
      <Youtube size={size} />
    </span>
  </span>
);

const UserProfileSelector = ({ profiles, selectedUserId, onSelect, onConnect }) => {
  const [isOpen, setIsOpen] = useState(false);

  if (!profiles || profiles.length === 0) return null;

  const selectedProfile = profiles.find(p => p.username === selectedUserId) || profiles[0];
  const autoId = isAutoProfileId(selectedProfile?.username);

  return (
    <div className="relative z-50">
      <button
        onClick={() => setIsOpen(!isOpen)}
        aria-label="social profile"
        /* Phone: avatar + chevron only. A 180px pill next to the menu button,
           the section title and the minutes meter overflowed a 360px header. */
        className="flex items-center justify-between gap-1 bg-paper2 border border-rule2 rounded-input px-2 sm:px-3 py-2 text-sm text-ink2 hover:bg-paper3 transition-colors sm:min-w-[180px]"
      >
        <span className="flex items-center gap-2">
          <div className="w-5 h-5 rounded-full bg-paper3 border border-rule flex items-center justify-center font-mono text-micro text-brass shrink-0">
            {autoId ? "S" : (selectedProfile?.username?.substring(0, 1).toUpperCase() || "U")}
          </div>
          {autoId ? (
            <span className="hidden sm:flex"><ProfileNetworkIcons profile={selectedProfile} size={13} /></span>
          ) : (
            <span className="hidden sm:block font-medium text-ink truncate max-w-[100px]">{selectedProfile?.username || "Select User"}</span>
          )}
        </span>
        <ChevronDown size={14} className={`text-muted transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute top-full mt-2 right-0 w-64 card overflow-hidden">
          <div className="max-h-60 overflow-y-auto custom-scrollbar">
            {profiles.map((profile) => (
              <button
                key={profile.username}
                onClick={() => {
                  onSelect(profile.username);
                  setIsOpen(false);
                }}
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-paper3 transition-colors text-left group border-b border-rule last:border-0"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-paper3 flex items-center justify-center font-mono text-micro text-ink border border-rule shrink-0">
                    {isAutoProfileId(profile.username) ? "S" : profile.username.substring(0, 2).toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-ink2 group-hover:text-ink transition-colors truncate">
                      {isAutoProfileId(profile.username)
                        ? `Social profile ${profiles.indexOf(profile) + 1}`
                        : profile.username}
                    </div>
                    <div className="flex gap-2 mt-0.5">
                      {/* Status indicators */}
                      <div className={`flex items-center gap-1 ${profile.connected.includes('tiktok') ? 'text-ink2' : 'text-muted opacity-40'}`}>
                        <TikTokIcon size={10} />
                      </div>
                      <div className={`flex items-center gap-1 ${profile.connected.includes('instagram') ? 'text-ink2' : 'text-muted opacity-40'}`}>
                        <Instagram size={10} />
                      </div>
                      <div className={`flex items-center gap-1 ${profile.connected.includes('youtube') ? 'text-ink2' : 'text-muted opacity-40'}`}>
                        <Youtube size={10} />
                      </div>
                    </div>
                  </div>
                </div>
                {selectedUserId === profile.username && <Check size={14} className="text-brass shrink-0" />}
              </button>
            ))}
          </div>
          {/* For managed users this dropdown otherwise does nothing (one profile,
              nothing to switch) — its real job is being the door to connecting
              the greyed-out networks it displays. */}
          {onConnect && (
            <button
              onClick={() => { setIsOpen(false); onConnect(); }}
              className="w-full flex items-center gap-2 px-4 py-3 text-sm text-brass hover:bg-paper3 transition-colors text-left border-t border-rule"
            >
              <Share2 size={14} /> Connect / manage accounts
            </button>
          )}
        </div>
      )}
    </div>
  );
};

const SESSION_KEY = 'openshorts_session_v2';

// The server's own explanation for a rejected/failed job, readable: FastAPI
// answers {"detail": "..."} or {"detail": {"message": ...}}. Showing the raw
// JSON (or a generic "failed") hid actionable reasons such as "this video is
// only 30s long", and new users in the tutorial saw only "That run failed".
const readableError = (raw) => {
  const text = String(raw || '').trim();
  try {
    const body = JSON.parse(text);
    const d = body?.detail ?? body;
    if (typeof d === 'string' && d) return d.slice(0, 300);
    if (d && typeof d.message === 'string') return d.message.slice(0, 300);
  } catch (_) { /* not JSON */ }
  return text.replace(/^Error:\s*/, '').slice(0, 300) || 'Something went wrong.';
};
// Matches the self-host JOB_RETENTION_SECONDS default. A restore whose job was
// already purged server-side (Cloud keeps it 1 h) gets a 404 from the status
// poll, which clears the saved session (see lib/jobPoll.js).
const SESSION_MAX_AGE = 86400000; // 24 hours

const pollJob = async (jobId) => {
  const res = await apiFetch(`/api/status/${jobId}`);
  if (!res.ok) throw new JobPollError(res.status);
  return res.json();
};

function App() {
  // Cloud auth/billing session (inert when billing is disabled).
  const { billingEnabled, isManaged, isSignedIn, me, plan, refreshMe, jobRetentionSeconds, localLlm, loading: authLoading } = useAuth();
  const [showLogin, setShowLogin] = useState(false);
  const [showTopUp, setShowTopUp] = useState(false);
  // Free plan: "want the watermark off?" once per job, when the clips land.
  const [showWmNotice, setShowWmNotice] = useState(false);
  const wmNoticedJobRef = useRef(null);
  const [showPlanChoice, setShowPlanChoice] = useState(false);
  const [tutorialPhase, setTutorialPhase] = useState(null); // null | intro | coach | celebrate
  const [showTrialUpgrade, setShowTrialUpgrade] = useState(false);
  const [topUpInfo, setTopUpInfo] = useState({});
  // {processed_minutes, total_minutes} when the running/finished job clips
  // only the first part of the source (the quota wall's free offer).
  const [partialJob, setPartialJob] = useState(null);
  // The free plan's first video, clipped whole past the 20-minute balance.
  const [firstVideoJob, setFirstVideoJob] = useState(false);
  // {position, ahead, eta_seconds} while the job waits in line, else null.
  const [queueInfo, setQueueInfo] = useState(null);
  // Why the last job could not start or failed, in plain words (or '').
  const [jobError, setJobError] = useState('');
  // Durable R2 URLs (per clip index) for the current job — used as a fallback when
  // the ephemeral local /videos/ files have been cleaned up (e.g. after a reload).
  const [durableClips, setDurableClips] = useState({});

  const [apiKey, setApiKey] = useState(localStorage.getItem('gemini_key') || '');
  // Social API State - Load encrypted or plain
  const [uploadPostKey, setUploadPostKey] = useState(() => {
    const stored = localStorage.getItem('uploadPostKey_v3');
    if (stored) return decrypt(stored);
    return '';
  });
  // ElevenLabs API State - Load encrypted
  const [elevenLabsKey, setElevenLabsKey] = useState(() => {
    const stored = localStorage.getItem('elevenLabsKey_v1');
    if (stored) return decrypt(stored);
    return '';
  });

  // fal.ai API State - Load encrypted
  const [falKey, setFalKey] = useState(() => {
    const stored = localStorage.getItem('falKey_v1');
    if (stored) return decrypt(stored);
    return '';
  });

  const [uploadUserId, setUploadUserId] = useState(() => localStorage.getItem('uploadUserId') || '');
  const [userProfiles, setUserProfiles] = useState([]); // List of {username, connected: []}
  // Post-generation social nudge: shown at the results peak until the user
  // either connects a network or dismisses it. Only 2.7% of cloud users who
  // reach the social flow ever connect an account — this is the moment (clips
  // just appeared) with the best odds of moving that number.
  const [socialNudgeDismissed, setSocialNudgeDismissed] = useState(() => {
    try { return localStorage.getItem('os_social_nudge_dismissed') === '1'; } catch (_) { return false; }
  });
  // Autopilot nudge, same results peak, ahead of the social one: the sign-up
  // survey's "autopilot" goal pays about twice the "clips" one, and 95% of
  // users never come back a second day; Autopilot is the one feature that
  // works without them coming back. Gone once clicked or dismissed.
  const [autopilotNudgeDismissed, setAutopilotNudgeDismissed] = useState(() => {
    try { return localStorage.getItem('os_autopilot_nudge_dismissed') === '1'; } catch (_) { return false; }
  });
  const [autopilotOn, setAutopilotOn] = useState(false);
  const [showKeyModal, setShowKeyModal] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState('idle'); // idle, processing, complete, error
  const [isStopping, setIsStopping] = useState(false);
  const [stopError, setStopError] = useState('');
  const stoppingRef = useRef(false);
  const [results, setResults] = useState(null);
  const removedVideos = useRef({ clip_ids: [], job_ids: [] });
  // Best clips first. The backend hands them back in transcript order, which
  // buries the strongest one wherever it happens to fall in the video — and
  // the first card is the one people actually watch and publish.
  //
  // The ORIGINAL array position travels with each clip and is what gets passed
  // down as `index`: it is the clip's identity everywhere else (clip_index on
  // /api/subtitle, /api/edit and publishing, the clip-N.mp4 download name, the
  // saved per-clip project state). Sorting the array itself would silently
  // repoint all of that at the wrong clip.
  const rankedClips = useMemo(() => {
    const clips = results?.clips;
    if (!Array.isArray(clips)) return [];
    return clips
      .map((clip, index) => ({ clip, index: clip.index ?? index }))
      .filter(({ clip, index }) => !clip.deleted && !isRemovedVideo(removedVideos.current, { jobId, clipIndex: index }))
      .sort((a, b) => {
        const sa = Number.isFinite(a.clip?.predicted_score) ? a.clip.predicted_score : -1;
        const sb = Number.isFinite(b.clip?.predicted_score) ? b.clip.predicted_score : -1;
        // Ties (and clips with no score at all) keep transcript order.
        return sb - sa || a.index - b.index;
      });
  }, [results, jobId]);
  // Bulk subtitles: apply one style to every clip of the job (triggered from
  // within a clip's subtitle modal via "apply to all").
  const [bulkSub, setBulkSub] = useState({ running: false, current: 0, total: 0, errors: 0 });
  const [downloadingAll, setDownloadingAll] = useState(false);
  // Pre-flight quality gate: { info: {max_height, min_height, cookies_invalid}, data }
  const [qualityGate, setQualityGate] = useState(null);
  const [logs, setLogs] = useState([]);
  // When each server log line arrived (epoch s, parallel to logs), from /api/status.
  const [logTimes, setLogTimes] = useState([]);
  // Collapsed on phones: the log tail is the least useful thing on a 360px
  // screen and it was pushing the actual clips a full scroll down.
  const [logsVisible, setLogsVisible] = useState(() => {
    try { return window.innerWidth >= 768; } catch { return true; }
  });
  const [processingMedia, setProcessingMedia] = useState(null);
  const [activeTab, setActiveTab] = useState('dashboard'); // dashboard, settings
  // Mobile only: the full nav lives in a drawer behind the header's menu button.
  const [navOpen, setNavOpen] = useState(false);
  // Reopened-project state (paid mode): per-clip {index, server_file, active_layers}
  // restored from the backend so ResultCards resume editing where they left off.
  const [projectState, setProjectState] = useState(null);
  // True when the current job was reopened from the library: its source video
  // was never persisted, so the session must not fall back to /api/source.
  const [noSource, setNoSource] = useState(false);

  const [sessionRecovered, setSessionRecovered] = useState(false);
  // The job a restored session was polling is no longer on the server.
  const [jobGone, setJobGone] = useState(false);
  const [showScheduleWeek, setShowScheduleWeek] = useState(false);
  // Clip editor overlay: index of the clip being edited, or null.
  const [editingClip, setEditingClip] = useState(null);
  const [libraryEditor, setLibraryEditor] = useState(null);
  const [libraryRevision, setLibraryRevision] = useState(0);
  const [editorRoute, setEditorRoute] = useState(() => parseEditorRoute(window.location.hash));
  useEffect(() => {
    const syncEditorRoute = () => setEditorRoute(parseEditorRoute(window.location.hash));
    window.addEventListener('hashchange', syncEditorRoute);
    window.addEventListener('popstate', syncEditorRoute);
    return () => {
      window.removeEventListener('hashchange', syncEditorRoute);
      window.removeEventListener('popstate', syncEditorRoute);
    };
  }, []);
  const campaigns = useCampaigns();
  useEffect(() => { if (jobId) campaigns.refresh(); }, [jobId, status, results?.clips?.length]);
  const [reframingClip, setReframingClip] = useState(null);

  // Silent-success "saved" states for the settings key inputs (design.md: no alert popups)
  const [elevenLabsSaved, setElevenLabsSaved] = useState(false);
  const [falSaved, setFalSaved] = useState(false);

  // Sync state for original video playback
  const [syncedTime, setSyncedTime] = useState(0);
  const [isSyncedPlaying, setIsSyncedPlaying] = useState(false);
  const [syncTrigger, setSyncTrigger] = useState(0);

  const handleClipPlay = (startTime) => {
    // Clip times are relative to the processed stretch; the preview plays the
    // whole source, so a picked range shifts them by its start.
    setSyncedTime(startTime + (Number(partialJob?.start_seconds) || 0));
    setIsSyncedPlaying(true);
    setSyncTrigger(prev => prev + 1);
  };

  const handleClipPause = () => {
    setIsSyncedPlaying(false);
  };

  // --- Project persistence (paid mode) ---
  // Debounced sync of each clip's browser-only edit state (Remotion layers +
  // current server file) to the backend, so a reopened project resumes intact.
  const clipStateSync = useRef({ jobId: null, pending: {}, files: {}, timer: null });
  // Read by in-flight async chases to notice that the user moved on to another job.
  const jobIdRef = useRef(jobId);
  useEffect(() => { jobIdRef.current = jobId; }, [jobId]);

  useEffect(() => {
    const onRemoved = event => {
      const removed = event.detail;
      removedVideos.current = { clip_ids: [...removedVideos.current.clip_ids, ...(removed.clip_ids || [])], job_ids: [...removedVideos.current.job_ids, ...(removed.job_ids || [])] };
      const matches = index => isRemovedVideo(removed, { jobId, clipIndex: index });
      setEditingClip(current => current !== null && matches(current) ? null : current);
      setReframingClip(current => current !== null && matches(current) ? null : current);
      setLibraryEditor(current => current && isRemovedVideo(removed, { jobId: current.jobId, clipIndex: current.index }) ? null : current);
      setResults(previous => !previous?.clips ? previous : { ...previous, clips: previous.clips.map((clip, index) => matches(clip.index ?? index) ? { deleted: true, index: clip.index ?? index, video_url: null } : clip) });
      setProjectState(previous => !previous?.clips ? previous : { ...previous, clips: previous.clips.filter(clip => !matches(clip.index)) });
      setDurableClips(previous => Object.fromEntries(Object.entries(previous).filter(([index]) => !matches(Number(index)))));
      const sync = clipStateSync.current;
      if (sync.jobId === jobId) {
        for (const index of Object.keys(sync.pending)) if (matches(Number(index))) delete sync.pending[index];
        for (const index of Object.keys(sync.files)) if (matches(Number(index))) delete sync.files[index];
      }
    };
    window.addEventListener(VIDEO_REMOVED_EVENT, onRemoved);
    return () => window.removeEventListener(VIDEO_REMOVED_EVENT, onRemoved);
  }, [jobId]);

  const flushClipState = () => {
    const s = clipStateSync.current;
    if (s.timer) { clearTimeout(s.timer); s.timer = null; }
    const entries = Object.entries(s.pending);
    if (!s.jobId || entries.length === 0) return;
    const clips = entries.map(([i, v]) => ({
      index: Number(i),
      active_layers: v.activeLayers,
      server_file: v.serverVideoFile,
    }));
    s.pending = {};
    apiFetch(`/api/projects/${s.jobId}/state`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clips }),
    }).catch(() => {});
  };

  // One /api/history read, reduced to this job's clips.
  const fetchDurableMap = async () => {
    const d = await apiJson('/api/history');
    const map = {};
    for (const v of (d.videos || [])) {
      if (v.job_id === jobId && v.clip_index != null) map[v.clip_index] = { url: v.view_url, filename: v.filename };
    }
    return map;
  };

  // A server-side edit rewrites the clip's file while the R2 re-archive behind it
  // is still in flight (_archive_clip_edit_bg is fire-and-forget), so the durable
  // map goes stale and the card falls back to streaming from the API. Re-read the
  // map on a backoff until the archived name matches the clip's new file, then it
  // can play from R2 again. Gives up quietly: staying on /videos is correct, just
  // slower, and is exactly what happens for self-hosted users all the time.
  // opts.source / opts.highlight: which surface opened it (CheckoutStarted
  // source) and which plan the modal puts forward (WatermarkModal: starter).
  // opts.partialJob: the trimmed job on screen, so the modal sells the rest of it.
  const openUpsell = (opts = {}) => {
    setTopUpInfo({ context: 'upsell', source: opts.source, highlight: opts.highlight,
                   partialJob: opts.partialJob || null });
    setShowTopUp(true);
  };

  // Out-of-minutes errors from the thumbnail studio, clip editor and autopilot
  // (lib/api.openQuotaWall) open the same wall as the main clip flow.
  const meStatus = me?.status;
  useEffect(() => {
    if (!isManaged) return undefined;
    const onWall = (ev) => {
      refreshMe();
      if (meStatus === 'trialing') { setShowTrialUpgrade(true); return; }
      const d = ev.detail || {};
      setTopUpInfo({ context: 'wall', required: d.required, remaining: d.remaining, source: d.source });
      setShowTopUp(true);
    };
    window.addEventListener(QUOTA_WALL_EVENT, onWall);
    return () => window.removeEventListener(QUOTA_WALL_EVENT, onWall);
  }, [isManaged, meStatus, refreshMe]);

  // The clips just landed on a free account: ask once, per job, whether they
  // want the mark off. A beat after the grid renders, so the first thing they
  // see is their clips and not a modal over them.
  useEffect(() => {
    if (status !== 'complete' || plan !== 'free' || !isManaged || !jobId) return;
    if (!(results?.clips?.length > 0)) return;
    // Not on top of the tutorial's celebrate step: it shows once that closes.
    if (tutorialPhase === 'coach' || tutorialPhase === 'celebrate') return;
    if (wmNoticedJobRef.current === jobId || watermarkNoticeDismissed(jobId)) return;
    wmNoticedJobRef.current = jobId;
    const t = setTimeout(() => { if (jobIdRef.current === jobId) setShowWmNotice(true); }, 2500);
    return () => clearTimeout(t);
  }, [status, plan, isManaged, jobId, results?.clips?.length, tutorialPhase]);

  // Paying re-points the clips already on screen at their clean twins (the API
  // does it from the Stripe webhook, a few seconds after the plan flips). Chase
  // the job result until no served file carries the wm_ prefix, then refresh
  // the durable map so the players switch to the clean R2 copies too.
  useEffect(() => {
    if (!isManaged || !jobId || !plan || plan === 'free') return;
    const isMarked = (c) => /\/wm_[^/]*$/.test(c?.video_url || '');
    if (!(results?.clips || []).some(isMarked)) return;
    let cancelled = false;
    (async () => {
      for (const delay of [1500, 3000, 6000, 12000, 20000]) {
        await new Promise((r) => setTimeout(r, delay));
        if (cancelled || jobIdRef.current !== jobId) return;
        let data;
        try { data = await pollJob(jobId); } catch { continue; }
        if (cancelled || jobIdRef.current !== jobId) return;
        if (data?.result) setResults(data.result);
        if (!(data?.result?.clips || []).some(isMarked)) {
          fetchDurableMap().then((m) => { if (!cancelled) setDurableClips(m); }).catch(() => {});
          return;
        }
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan, isManaged, jobId]);

  const chaseDurableFile = async (index, expectedFile) => {
    const forJob = jobId;
    for (const delay of [2500, 6000, 15000, 30000]) {
      await new Promise((r) => setTimeout(r, delay));
      if (jobIdRef.current !== forJob) return;
      let map;
      try { map = await fetchDurableMap(); } catch { return; }
      // A new job started mid-chase: this map describes the old one, so dropping
      // it here keeps it from overwriting the new job's URLs.
      if (jobIdRef.current !== forJob) return;
      setDurableClips(map);
      if (map[index]?.filename === expectedFile) return;
    }
  };

  const handleClipStateChange = (index, state) => {
    if (!isManaged || !jobId) return;
    const s = clipStateSync.current;
    if (s.jobId !== jobId) { s.pending = {}; s.files = {}; s.jobId = jobId; }
    s.pending[index] = state;
    // Cards report on mount too, so only an actual change of server file means an
    // edit just landed. The first report per clip is the mount, never a chase.
    const files = s.files || (s.files = {});
    const file = state?.serverVideoFile;
    if (file && files[index] !== file) {
      const isMount = files[index] === undefined;
      files[index] = file;
      if (!isMount) chaseDurableFile(index, file);
    }
    if (s.timer) clearTimeout(s.timer);
    s.timer = setTimeout(flushClipState, 2000);
  };

  // A recut replaced the clip's server file with a fresh render (burned layers
  // reset), so update the results, the reopened-project state and the synced
  // per-clip edit state, and let the ResultCard remount from the new file.
  const handleClipRerendered = (index, data) => {
    const newFile = (data.new_video_url || '').split('/').pop();
    setResults((prev) => {
      if (!prev?.clips?.some((clip, i) => !clip.deleted && (clip.index ?? i) === index)) return prev;
      const clips = prev.clips.map((clip, i) => (clip.index ?? i) !== index ? clip : {
        ...clip,
        video_url: data.new_video_url,
        start: data.start,
        end: data.end,
        recipe: data.recipe,
        _aiEditCardKey: null,
      });
      return { ...prev, clips };
    });
    setProjectState((prev) => {
      if (!prev?.clips) return prev;
      return {
        ...prev,
        clips: prev.clips.map((c) => (c.index === index
          ? { ...c, server_file: newFile, active_layers: null }
          : c)),
      };
    });
    // The old durable R2 object is deleted when the recut is archived, so the
    // stale URL would 404 as a fallback; drop it until the next refresh.
    setDurableClips((prev) => {
      if (!(index in prev)) return prev;
      const next = { ...prev };
      delete next[index];
      return next;
    });
    handleClipStateChange(index, { activeLayers: null, serverVideoFile: newFile });
  };

  const handleClipVideoUpdated = (index, data) => {
    const timeline = Object.fromEntries(['duration_seconds', 'start', 'end', 'recipe', 'layout_ranges', 'caption_words', 'source_edits', 'source_visuals', 'auto_hook']
      .filter(key => data[key] !== undefined).map(key => [key, data[key]]));
    setResults(previous => {
      if (!previous?.clips?.some((clip, i) => !clip.deleted && (clip.index ?? i) === index)) return previous;
      return { ...previous, clips: previous.clips.map((clip, i) => (clip.index ?? i) === index
        ? { ...clip, ...timeline, _aiEditCardKey: clip._aiEditCardKey ?? `${clip.start}-${clip.end}-${JSON.stringify(clip.recipe || null)}`, video_url: data.new_video_url, ai_edit: {
          ...clip.ai_edit, preview_start: data.preview_start, preview_end: data.preview_end,
        } } : clip) };
    });
    setProjectState(previous => !previous?.clips ? previous : { ...previous,
      clips: previous.clips.map(clip => clip.index === index
        ? { ...clip, server_file: data.new_video_url.split('/').pop() } : clip),
    });
  };

  // Reopen an archived project from the History tab: the backend re-downloads
  // its files from R2 into the server's working dir and returns the full state.
  const restoreProject = async (projectJobId) => {
    const data = await apiJson(`/api/projects/${projectJobId}/restore`, { method: 'POST' });
    flushClipState();
    setProjectState(data.project_state || null);
    setNoSource(true);
    setJobId(data.job_id);
    setResults(data.result || null);
    setLogs(['♻️ Project restored from your library.']);
    setLogTimes([Date.now() / 1000]);
    setProcessingMedia(null);
    setQualityGate(null);
    setStatus('complete');
    setActiveTab('dashboard');
  };

  // Apply one subtitle style to every clip of the job, sequentially.
  const handleBulkSubtitles = async (options) => {
    const clips = (results?.clips || []).map((clip, index) => ({ ...clip, index: clip.index ?? index })).filter(clip => !clip.deleted && !isRemovedVideo(removedVideos.current, { jobId, clipIndex: clip.index }));
    const total = clips.length;
    if (!total) return;
    setBulkSub({ running: true, current: 0, total, errors: 0 });
    let errors = 0;
    for (let i = 0; i < total; i++) {
      setBulkSub({ running: true, current: i + 1, total, errors });
      try {
        const res = await apiFetch('/api/subtitle', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            job_id: jobId,
            clip_index: clips[i].index,
            position: options.position,
            font_size: options.fontSize,
            font_name: options.fontName,
            font_color: options.fontColor,
            border_color: options.borderColor,
            border_width: options.borderWidth,
            bg_color: options.bgColor,
            bg_opacity: options.bgOpacity,
            preset: options.preset || null,
            style: options.style || 'karaoke',
            highlight_color: options.highlightColor || '#FFD700',
            effect: options.effect || 'none',
            base_opacity: options.baseOpacity ?? 1.0,
            uppercase: options.uppercase || false,
            reveal: options.reveal || false,
            shadow: options.shadow || 0,
            max_chars: options.maxChars ?? null,
            max_duration: options.maxDuration ?? null,
            // Chain from the clip's current server file (its video_url basename).
            input_filename: (clips[i].video_url || '').split('/').pop(),
          }),
        });
        if (!res.ok) errors++;
      } catch {
        errors++;
      }
    }
    setBulkSub({ running: false, current: total, total, errors });
    refreshMe();
    // Refresh results so each ResultCard picks up its new subtitled video_url.
    try {
      const data = await pollJob(jobId);
      if (data.result) setResults(data.result);
    } catch { /* keep current results */ }
  };

  const handleDownloadAll = async () => {
    if (!jobId) return;
    setDownloadingAll(true);
    try {
      const res = await apiFetch(`/api/jobs/${jobId}/download-all`);
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `openshorts_clips_${(jobId || '').slice(0, 8)}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(`Download failed: ${e.message}`);
    } finally {
      setDownloadingAll(false);
    }
  };

  // Session Recovery: Restore on mount
  useEffect(() => {
    try {
      localStorage.removeItem('openshorts_session');
      const saved = localStorage.getItem(SESSION_KEY);
      if (!saved) return;
      const session = JSON.parse(saved);
      if (Date.now() - session.timestamp > SESSION_MAX_AGE) {
        localStorage.removeItem(SESSION_KEY);
        return;
      }
      if (session.jobId && session.status && session.status !== 'idle') {
        jobIdRef.current = session.jobId;
        setJobId(session.jobId);
        setResults(session.results || null);
        // Restore the source preview. Older sessions (or uploads) saved no
        // media, so fall back to the backend-served source for this job —
        // except for reopened projects, whose source was never persisted.
        if (session.processingMedia) setProcessingMedia(session.processingMedia);
        else if (!session.noSource) setProcessingMedia({ type: 'server', payload: `/api/source/${session.jobId}` });
        if (session.noSource) setNoSource(true);
        if (session.projectState) setProjectState(session.projectState);
        if (session.activeTab) setActiveTab(session.activeTab);
        // If was processing, resume polling; if complete/error, just show results
        setStatus(session.status === 'processing' ? 'processing' : session.status);
        // Completed sessions do not poll. Reconcile their cached URLs once so
        // a server edit (including one from another tab) survives a reload.
        if (session.status === 'complete') {
          pollJob(session.jobId).then(data => {
            if (jobIdRef.current !== session.jobId) return;
            if (data.result) setResults(data.result);
          }).catch(() => {});
        }
        setSessionRecovered(true);
        setTimeout(() => setSessionRecovered(false), 5000);
      }
    } catch (e) {
      localStorage.removeItem(SESSION_KEY);
    }
  }, []);

  // Session Recovery: Save state changes
  useEffect(() => {
    if (status === 'idle') {
      localStorage.removeItem(SESSION_KEY);
      return;
    }
    try {
      // URL (YouTube) media serializes as-is. Uploaded 'file' media is a blob
      // that can't be persisted, so point the recovered preview at the source
      // served by the backend instead of dropping it.
      let persistMedia = null;
      if (processingMedia?.type === 'url') persistMedia = processingMedia;
      else if (processingMedia && jobId) persistMedia = { type: 'server', payload: `/api/source/${jobId}` };
      const sessionData = {
        jobId,
        status,
        results,
        processingMedia: persistMedia,
        activeTab,
        noSource,
        projectState,
        timestamp: Date.now()
      };
      localStorage.setItem(SESSION_KEY, JSON.stringify(sessionData));
    } catch (e) {
      // localStorage full or serialization error - ignore
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, status, results, activeTab, noSource, projectState]);

  useEffect(() => {
    // Encrypt Gemini Key too for consistency if desired, but user asked specifically about Social integration not saving well.
    // For now keeping gemini plain for compatibility unless requested.
    if (apiKey) localStorage.setItem('gemini_key', apiKey);
  }, [apiKey]);

  useEffect(() => {
    if (uploadPostKey) {
      localStorage.setItem('uploadPostKey_v3', encrypt(uploadPostKey));
    }
    if (uploadUserId) {
      localStorage.setItem('uploadUserId', uploadUserId);
    }
  }, [uploadPostKey, uploadUserId]);

  useEffect(() => {
    if (elevenLabsKey) {
      localStorage.setItem('elevenLabsKey_v1', encrypt(elevenLabsKey));
    }
  }, [elevenLabsKey]);

  useEffect(() => {
    if (falKey) {
      localStorage.setItem('falKey_v1', encrypt(falKey));
    }
  }, [falKey]);

  useEffect(() => {
    if ((uploadPostKey || isManaged) && userProfiles.length === 0) {
      fetchUserProfiles({ silent: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadPostKey, isManaged]);

  // For managed users, fetch the durable R2 URLs of the current job's clips. The
  // preview player prefers them (free egress, edge-served, and not competing with
  // the renders for the API process), and falls back to /videos when the local
  // file is newer than the archived one or the signed link fails.
  // Kept fresh by chaseDurableFile after each edit and by the completion chase
  // below; until either lands, the card just streams from /videos.
  useEffect(() => {
    if (!isManaged || !jobId || !(results?.clips?.length)) { setDurableClips({}); return; }
    let cancelled = false;
    fetchDurableMap()
      .then((map) => { if (!cancelled) setDurableClips(map); })
      .catch(() => {});
    return () => { cancelled = true; };
    // Keyed on the clip COUNT, not on results: the status poll hands back a new
    // results object every couple of seconds while the job runs, and this used to
    // re-read the history on every one of them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isManaged, jobId, results?.clips?.length]);

  // A job is marked complete BEFORE _archive_managed_job has finished uploading
  // (app.py:878), so the read above can land while R2 still has nothing for it and
  // every card would stream from the API for the rest of the session. Chase until
  // all clips have a durable copy.
  useEffect(() => {
    const count = results?.clips?.length || 0;
    if (!isManaged || !jobId || status !== 'complete' || !count) return;
    let cancelled = false;
    (async () => {
      for (const delay of [0, 3000, 8000, 20000, 40000]) {
        if (delay) await new Promise((r) => setTimeout(r, delay));
        if (cancelled) return;
        let map;
        try { map = await fetchDurableMap(); } catch { return; }
        if (cancelled) return;
        setDurableClips(map);
        if (Object.keys(map).length >= count) return;
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isManaged, jobId, status, results?.clips?.length]);

  // Read at failure time: the poll effect must not restart when auth settles.
  const historyAvailableRef = useRef(false);
  historyAvailableRef.current = billingEnabled && isSignedIn;
  const handleResetRef = useRef(null);

  useEffect(() => {
    let interval;
    let failures = 0;
    let active = true;
    if ((status === 'processing' || status === 'completed') && jobId) {
      interval = setInterval(async () => {
        try {
          const data = await pollJob(jobId);
          if (!active || stoppingRef.current || jobIdRef.current !== jobId) return;
          failures = 0;
          console.log("Job status:", data);

          // Update results if available (real-time)
          if (data.result) {
            setResults(data.result);
          }

          if (data.partial) setPartialJob(data.partial);
          setQueueInfo(data.status === 'queued' && data.queue ? data.queue : null);

          if (data.status === 'completed') {
            setQueueInfo(null);
            setStatus('complete');
            clearInterval(interval);
            refreshMe();
          } else if (data.status === 'cancelled') {
            clearInterval(interval);
            handleResetRef.current();
            refreshMe();
          } else if (data.status === 'failed') {
            setStatus('error');
            const errorMsg = data.error || (data.logs && data.logs.length > 0 ? data.logs[data.logs.length - 1] : "Process failed");
            setJobError(readableError(errorMsg));
            setLogs(prev => [...prev, "Error: " + errorMsg]);
            clearInterval(interval);
            refreshMe();
          } else {
            // Update logs if available
            if (data.logs) {
              setLogs(data.logs);
              setLogTimes(data.log_times || []);
            }
          }
        } catch (e) {
          if (!active || stoppingRef.current || jobIdRef.current !== jobId) return;
          console.error("Polling error", e);
          failures += 1;
          if (pollFailureAction(e, failures) === 'gone') {
            clearInterval(interval);
            // Back to idle (which drops the saved session) and on to History,
            // where a finished job's clips are kept.
            handleResetRef.current();
            setSessionRecovered(false);
            setJobGone(true);
            if (historyAvailableRef.current) setActiveTab('history');
          }
        }
      }, 2000);
    }
    return () => { active = false; clearInterval(interval); };
  }, [status, jobId, refreshMe]);


  // silent: background auto-fetch — never alert(), just log. Managed users need
  // no local key (the server resolves its own); BYOK sends the header.
  const fetchUserProfiles = async ({ silent = false } = {}) => {
    if (!uploadPostKey && !isManaged) return;
    try {
      const res = await apiFetch('/api/social/user', {
        headers: uploadPostKey ? { 'X-Upload-Post-Key': uploadPostKey } : {}
      });
      if (!res.ok) throw new Error("Failed to fetch");
      const data = await res.json();
      if (data.profiles && data.profiles.length > 0) {
        setUserProfiles(data.profiles);
        // Auto select first if none selected
        if (!uploadUserId) {
          setUploadUserId(data.profiles[0].username);
        }
      } else if (!silent) {
        alert("No profiles found for this API Key.");
      }
    } catch (e) {
      if (!silent) alert("Error fetching User Profiles. Please check key.");
      console.error(e);
    }
  };

  // Hosted is paid-only (no BYOK core). Self-host uses BYOK keys.
  // `keysMissing` now means "self-host BYOK keys missing" — it never fires on hosted.
  // A self-hosted server running the moment picker on a local LLM
  // (LLM_BASE_URL) does not need a Gemini key for the core pipeline.
  const geminiOk = !!apiKey || !!localLlm;
  // Local clipping needs an analysis provider; a publishing key is optional.
  const keysMissing = !billingEnabled && !authLoading && !geminiOk;
  const needsPlan = billingEnabled && !isManaged;   // hosted, signed-out or no active plan/trial

  // Fresh sign-up: Clip Generator tutorial (AuthContext set os_show_clip_tutorial
  // after the auth redirect). QA: #app?tutorial=1. Resume coach if they refreshed
  // mid-job. Runs once on mount so a later isSignedIn flip cannot reset intro→coach.
  useEffect(() => {
    let showTutorial = false;
    let resumeCoach = false;
    try {
      const q = new URLSearchParams((window.location.hash.split('?')[1] || ''));
      const qa = q.get('tutorial');
      if (qa === '1') showTutorial = true;
      if (qa === 'coach') resumeCoach = true;
      if (qa === 'celebrate') { setTutorialPhase('celebrate'); return; }
      if (localStorage.getItem('os_show_clip_tutorial') === '1') showTutorial = true;
      if (localStorage.getItem('os_clip_tutorial') === 'coach') resumeCoach = true;
      // A request parked before the sign-in redirect is about to resume on its
      // own: opening the intro ("paste a link and generate") on top of a job
      // that is already starting contradicts itself. Skip it for this signup.
      if (showTutorial && peekPendingJob()) {
        showTutorial = false;
        localStorage.removeItem('os_show_clip_tutorial');
      }
    } catch (_) { /* ignore */ }
    if (showTutorial && billingEnabled) {
      setTutorialPhase('intro');
      setActiveTab('dashboard');
    } else if (resumeCoach) {
      setTutorialPhase('coach');
      setActiveTab('dashboard');
    }
  }, []);

  // Legacy: an older build may still have set os_show_plan_choice. Don't open it
  // on top of the tutorial.
  useEffect(() => {
    if (tutorialPhase) return;
    if (!(billingEnabled && isSignedIn)) return;
    let showPlans = false;
    try { showPlans = localStorage.getItem('os_show_plan_choice') === '1'; } catch (_) { /* ignore */ }
    if (showPlans) {
      setShowPlanChoice(true);
      try { localStorage.removeItem('os_show_plan_choice'); } catch (_) { /* ignore */ }
    }
  }, [billingEnabled, isSignedIn, tutorialPhase]);

  const tutorialLock = billingEnabled && (tutorialPhase === 'intro' || tutorialPhase === 'coach' || tutorialPhase === 'celebrate');

  // Sign-up survey (cloud/onboarding.py): before the tutorial intro, never
  // during the coach phase or the celebration. It may open while a job runs or
  // once it is done: a link pasted on the landing resumes straight into
  // processing after sign-in, and gating on 'idle' left ~half of all sign-ups
  // (the landing-paste flow, 98% of whom never come back) unasked.
  const [surveyDone, setSurveyDone] = useState(false);
  const showSurvey = billingEnabled && !!me?.onboarding_survey_pending && !surveyDone
    && (tutorialPhase === null || tutorialPhase === 'intro') && status !== 'error';

  useEffect(() => {
    if (tutorialLock && activeTab !== 'dashboard') setActiveTab('dashboard');
  }, [tutorialLock, activeTab]);

  // Deep links into a tab: #app?tab=autopilot (Autopilot emails, the social
  // connect page's return URL). Read once per hash change, then the query is
  // dropped so a reload does not keep forcing the tab.
  const [autopilotConnected, setAutopilotConnected] = useState(null); // connect outcome, see lib/connectReturn.js
  useEffect(() => {
    const DEEP_LINK_TABS = ['library', 'settings', 'dashboard', 'history'];
    const apply = () => {
      const hash = window.location.hash || '';
      if (!hash.startsWith('#app?')) return;
      const params = new URLSearchParams(hash.slice(5));
      const tab = params.get('tab');
      if (!tab || !DEEP_LINK_TABS.includes(tab)) return;  // e.g. #app?tutorial=1
      setActiveTab(tab === 'history' ? 'library' : tab);
      // The connect outcome rides in the query string, not in the hash
      // (lib/connectReturn.js): a cancel also comes back with connected=1.
      if (tab === 'autopilot' && params.get('connected') === '1') {
        setAutopilotConnected(readConnectReturn(window.location.search));
      }
      try {
        window.history.replaceState(null, '', `${window.location.pathname}${stripConnectReturn(window.location.search)}#app`);
      } catch (_) { /* ignore */ }
    };
    apply();
    window.addEventListener('hashchange', apply);
    return () => window.removeEventListener('hashchange', apply);
  }, []);

  // Signed out on a tab that needs a session (the clips-ready email links to
  // #app?tab=history): remember it, so the sign-in comes back here instead of
  // to the Clip Generator (lib/tabAfterLogin, read by AuthContext).
  useEffect(() => {
    if (billingEnabled && !authLoading && !isSignedIn) rememberTabAfterLogin(activeTab);
  }, [billingEnabled, authLoading, isSignedIn, activeTab]);

  // #app?checkout=cancel: back from a Stripe checkout opened inside the app
  // (cancel_url in cloud/billing.py), so the user lands on their clips again.
  useEffect(() => {
    const hash = window.location.hash || '';
    if (!hash.startsWith('#app?') || new URLSearchParams(hash.slice(5)).get('checkout') !== 'cancel') return;
    track('CheckoutCanceled', { props: { back_to: 'app' } });
    try { window.history.replaceState(null, '', '#app'); } catch (_) { /* ignore */ }
  }, []);

  // #app?login=1: the "Log in" link of the site nav (seo/nav.js). Opens the
  // sign-in dialog once the session is known (already signed in: just the
  // app), then drops the query so a reload does not reopen it.
  const wantsLogin = () => {
    const hash = window.location.hash || '';
    return hash.startsWith('#app?') && new URLSearchParams(hash.slice(5)).get('login') === '1';
  };
  const [loginRequested, setLoginRequested] = useState(wantsLogin);
  useEffect(() => {
    const onHash = () => { if (wantsLogin()) setLoginRequested(true); };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  useEffect(() => {
    if (!loginRequested || authLoading) return;
    setLoginRequested(false);
    if (billingEnabled && !isSignedIn) setShowLogin(true);
    try { window.history.replaceState(null, '', '#app'); } catch (_) { /* ignore */ }
  }, [loginRequested, authLoading, billingEnabled, isSignedIn]);

  useEffect(() => {
    if (tutorialPhase === 'coach' && status === 'complete' && (results?.clips?.length > 0)) {
      setTutorialPhase('celebrate');
      track('ClipTutorialCompleted', { props: { clips: results.clips.length } });
    }
  }, [tutorialPhase, status, results]);

  const finishTutorial = () => {
    try { localStorage.setItem('os_clip_tutorial', 'done'); } catch (_) { /* ignore */ }
    try { localStorage.removeItem('os_show_clip_tutorial'); } catch (_) { /* ignore */ }
    setTutorialPhase(null);
  };
  const startTutorial = () => {
    try { localStorage.setItem('os_clip_tutorial', 'coach'); } catch (_) { /* ignore */ }
    try { localStorage.removeItem('os_show_clip_tutorial'); } catch (_) { /* ignore */ }
    track('ClipTutorialStarted');
    setTutorialPhase('coach');
    setActiveTab('dashboard');
  };
  const skipTutorial = () => {
    track('ClipTutorialSkipped', { props: { phase: tutorialPhase } });
    finishTutorial();
  };
  // Included in the plan (fully managed, no keys): Clip Generator + YouTube Studio.
  // Advanced (bring your own fal.ai + ElevenLabs keys): AI Shorts + AI Agent.
  const INCLUDED_TOOL_TABS = ['dashboard', 'thumbnails'];
  const ADVANCED_TOOL_TABS = ['saasshorts', 'ai-agent'];
  const TOOL_NAMES = { dashboard: 'the Clip Generator', thumbnails: 'the YouTube Studio' };
  const gateThisTab = needsPlan && INCLUDED_TOOL_TABS.includes(activeTab);      // included tool, no plan yet
  const advancedThisTab = billingEnabled && ADVANCED_TOOL_TABS.includes(activeTab); // BYOK-notice tools

  // Social nudge visibility: managed users with clips on screen and no network
  // connected yet. userProfiles being empty (not yet fetched / none created)
  // also counts as "not connected" — that is the 97% case.
  const connectedSocials = ((userProfiles.find((p) => p.username === uploadUserId) || userProfiles[0])?.connected) || [];
  const showAutopilotNudge = billingEnabled && isManaged && !autopilotNudgeDismissed && !autopilotOn && !tutorialLock;
  const showSocialNudge = isManaged && !socialNudgeDismissed && connectedSocials.length === 0 && !tutorialLock
    && !showAutopilotNudge;

  // Paid plans may already run Autopilot: ask once, on the first results.
  const autopilotCheckedRef = useRef(false);
  useEffect(() => {
    if (status !== 'complete' || !showAutopilotNudge || plan === 'free' || autopilotCheckedRef.current) return;
    autopilotCheckedRef.current = true;
    apiJson('/api/autopilot')
      .then((d) => { if (d?.settings?.enabled) setAutopilotOn(true); })
      .catch(() => { /* keep the nudge */ });
  }, [status, showAutopilotNudge, plan]);

  const dismissAutopilotNudge = (event) => {
    track(event);
    setAutopilotNudgeDismissed(true);
    try { localStorage.setItem('os_autopilot_nudge_dismissed', '1'); } catch (_) { /* ignore */ }
  };

  const autopilotNudgeSeenRef = useRef(null);
  useEffect(() => {
    if (status === 'complete' && (results?.clips?.length > 0) && showAutopilotNudge && autopilotNudgeSeenRef.current !== jobId) {
      autopilotNudgeSeenRef.current = jobId;
      track('AutopilotNudgeSeen', { props: { plan: plan || 'free' } });
    }
  }, [status, results, showAutopilotNudge, jobId, plan]);

  // One Seen event per job, only when the banner actually rendered.
  const socialNudgeSeenRef = useRef(null);
  useEffect(() => {
    if (status === 'complete' && (results?.clips?.length > 0) && showSocialNudge && socialNudgeSeenRef.current !== jobId) {
      socialNudgeSeenRef.current = jobId;
      track('SocialNudgeSeen', { props: { clips: results.clips.length } });
    }
  }, [status, results, showSocialNudge, jobId]);

  // Managed users connect their socials via Upload-Post's branded hosted page.
  const handleConnectSocials = async () => {
    try {
      const { access_url } = await apiJson('/api/social/connect', { method: 'POST' });
      // Same tab so the connect page's redirectUrl brings the user back into the app.
      if (access_url) window.location.href = access_url;
    } catch (e) {
      alert('Could not open the connection page. Please try again.');
    }
  };

  // Open the Upload-Post white-label page (which includes the scheduling calendar)
  // in a new tab, for consulting/managing scheduled posts from the dashboard.
  const handleOpenCalendar = async () => {
    try {
      const { access_url } = await apiJson('/api/social/connect', { method: 'POST' });
      if (access_url) window.open(access_url, '_blank', 'noopener');
    } catch (e) {
      alert('Could not open the calendar. Please try again.');
    }
  };

  const handleProcess = async (data, forceLowQuality = false) => {
    // Hosted: must be signed in AND on an active plan/trial. Self-host: BYOK keys.
    if (billingEnabled) {
      // The billing gate below is unchanged: signed in, then entitled, then the
      // processing path. The only new thing is the first branch remembering what
      // the visitor asked for before sending them to sign in, so the resume
      // effect can hand the exact same request back to this function and let it
      // fall through the same gates.
      if (!isSignedIn) { stashPendingJob(data); setShowLogin(true); return; }
      if (!isManaged) { window.location.hash = '#/pricing'; return; }
    } else if (keysMissing) {
      setShowKeyModal(true);
      return;
    }
    // Past every gate, so this request is really running: nothing left to resume.
    clearPendingJob();
    jobIdRef.current = null;
    setJobId(null);
    setStopError('');
    setStatus('processing');
    setJobError('');
    setLogs(["Starting process..."]);
    setLogTimes([Date.now() / 1000]);
    setResults(null);
    // Studio handovers have no local media object; the preview switches to the
    // backend-served source once the job id is known.
    setProcessingMedia(data.type === 'thumbnail_session' ? null : data);
    setQualityGate(null);
    setProjectState(null);
    setNoSource(false);
    setPartialJob(null);
    setFirstVideoJob(false);

    try {
      let body;
      // BYOK sends the Gemini header; managed users rely on the bearer token
      // that apiFetch attaches automatically.
      const headers = apiKey ? { 'X-Gemini-Key': apiKey } : {};

      // Advanced generation controls: only sent when the user set them, so the
      // default request stays byte-identical to the pre-feature one.
      const advanced = {
        category_name: data.categoryName || null,
        caption_preset: data.captionPreset || 'cc_mozi',
        target_clips: data.targetClips || null,
        clip_min_seconds: data.clipMinSeconds || null,
        clip_max_seconds: data.clipMaxSeconds || null,
        // Sent explicitly both ways: absent means off for raw API callers,
        // but the dashboard always states the user's choice.
        auto_hook: data.autoHook ? '1' : '0',
        auto_hook_style: data.autoHook ? (data.autoHookStyle || 'pill') : null,
        // 'auto' is the server default, so only a deliberate choice travels.
        layouts: data.layout && data.layout !== 'auto' ? data.layout : null,
        // Set when the user took the quota wall's "clip the first N minutes"
        // offer: the server reserves N minutes and cuts the source to them.
        max_minutes: data.maxMinutes || null,
        // The stretch picked under the cost line (seconds); absent = whole video.
        source_start: data.sourceStart ?? null,
        source_end: data.sourceEnd ?? null,
      };

      if (data.type === 'url') {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify({
          url: data.payload,
          acknowledged: !!data.acknowledged,
          output_format: data.outputFormat || 'auto',
          force_low_quality: forceLowQuality,
          ...Object.fromEntries(Object.entries(advanced).filter(([, v]) => v != null)),
        });
      } else if (data.type === 'thumbnail_session') {
        // Handover from Thumbnail Studio (issue #68): the video and transcript
        // already live server-side, keyed by the Studio session.
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify({
          thumbnail_session_id: data.payload,
          acknowledged: !!data.acknowledged,
          output_format: data.outputFormat || 'auto',
          ...Object.fromEntries(Object.entries(advanced).filter(([, v]) => v != null)),
        });
      } else {
        const formData = new FormData();
        formData.append('file', data.payload);
        formData.append('acknowledged', data.acknowledged ? 'true' : 'false');
        formData.append('output_format', data.outputFormat || 'auto');
        for (const [k, v] of Object.entries(advanced)) {
          if (v != null) formData.append(k, v);
        }
        body = formData;
      }

      const res = await apiFetch('/api/process', { method: 'POST', headers, body });

      if (!res.ok) throw new Error(await res.text());
      const resData = await res.json();

      // Quality gate: the source is below the min resolution — ask before burning
      // 20 min on it. On confirm we resend with force_low_quality.
      if (resData.needs_confirmation) {
        setStatus('idle');
        setQualityGate({ info: resData.quality_check, data });
        return;
      }

      setJobId(resData.job_id);
      campaigns.refresh();
      setPartialJob(resData.partial || null);
      setFirstVideoJob(!!resData.first_video);
      // The server clipped past the balance on its own (no wall): the first
      // video whole, or the first N minutes of a later one.
      if (resData.first_video) track('FirstVideoGrant');
      else if (resData.partial && !resData.partial.chosen && data.maxMinutes == null) {
        track('AutoPartial', { props: { processed: resData.partial.processed_minutes, total: resData.partial.total_minutes } });
      }
      if (data.type === 'thumbnail_session') {
        setProcessingMedia({ type: 'server', payload: `/api/source/${resData.job_id}` });
      }
      // Minutes are reserved at job start, not at complete.
      refreshMe();

    } catch (e) {
      if (e instanceof QuotaError && e.code === 'free_queue_full') {
        // The free queue is full: nothing was reserved or spent. Same plan
        // wall, its own copy and source so the funnel is measured apart.
        setStatus('idle');
        setTopUpInfo({ context: 'queue_full', source: 'queue_full', highlight: 'starter' });
        setShowTopUp(true);
        return;
      }
      if (e instanceof QuotaError) {
        setStatus('idle');
        refreshMe();
        // Trial users hit the trial minute cap → prompt them to activate the plan
        // now (unlocks full minutes). Active users → offer a top-up.
        if (me?.status === 'trialing') {
          setShowTrialUpgrade(true);
        } else {
          // The wall can offer the first N minutes of this same submission on
          // the minutes they have: same data, plus max_minutes.
          const partial = e.partialMinutes || 0;
          setTopUpInfo({
            required: e.minutesRequired,
            remaining: e.minutesRemaining,
            partialMinutes: partial,
            onPartial: partial
              ? () => {
                  track('PartialClipChosen', { props: { required: e.minutesRequired, partial } });
                  setShowTopUp(false);
                  handleProcess({ ...data, maxMinutes: partial }, forceLowQuality);
                }
              : null,
          });
          setShowTopUp(true);
        }
        return;
      }
      const reason = readableError(e.message);
      setJobError(reason);
      setStatus('error');
      setLogs(l => [...l, `Error starting job: ${reason}`]);
    }
  };

  // Resume the job the visitor started before signing in. Runs on the render
  // that first sees isSignedIn true — after a magic link or a Google round trip,
  // i.e. a fresh document — and replays the request through handleProcess, so
  // the entitlement gate still decides whether it actually runs: an unentitled
  // account lands on #/pricing with the request still parked, and pays for it
  // later. `resumedStamp` keeps one parked request from being replayed twice in
  // the same document (the pricing redirect would otherwise loop on it).
  const handleProcessRef = useRef(null);
  const resumedStampRef = useRef(0);
  useEffect(() => {
    handleProcessRef.current = handleProcess;
  });
  useEffect(() => {
    if (!billingEnabled || !isSignedIn) return;
    const pending = peekPendingJob();
    if (!pending || pending.stamp === resumedStampRef.current) return;
    resumedStampRef.current = pending.stamp;
    track('JobResumedAfterSignin', { props: { type: pending.data?.type || 'unknown' } });
    handleProcessRef.current(pending.data);
  }, [billingEnabled, isSignedIn]);

  const handleReset = () => {
    jobIdRef.current = null;
    setJobGone(false);
    // Flush any pending edit-state sync before dropping the project: the clips
    // themselves are already archived to R2 as they were edited.
    flushClipState();
    setStatus('idle');
    setJobId(null);
    setResults(null);
    setLogs([]);
    setLogTimes([]);
    setProcessingMedia(null);
    setProjectState(null);
    setNoSource(false);
    setPartialJob(null);
    setFirstVideoJob(false);
    setQueueInfo(null);
    setJobError('');
    localStorage.removeItem(SESSION_KEY);
    setStopError('');
  };
  useEffect(() => {
    handleResetRef.current = handleReset;
  });

  const handleStop = async () => {
    if (!jobId || stoppingRef.current) return;
    const stoppingJobId = jobId;
    stoppingRef.current = true;
    setIsStopping(true);
    setStopError('');
    try {
      await apiJson(`/api/jobs/${stoppingJobId}/cancel`, { method: 'POST' });
      if (jobIdRef.current !== stoppingJobId) return;
      handleReset();
      setSessionRecovered(false);
      setActiveTab('dashboard');
      refreshMe();
    } catch (error) {
      if (jobIdRef.current === stoppingJobId) setStopError(error.detail || 'Could not stop clip creation. Try again.');
    } finally {
      stoppingRef.current = false;
      setIsStopping(false);
    }
  };

  // --- UI Components ---

  // One nav definition drives all three surfaces: the desktop rail, the mobile
  // drawer, and the bottom tab bar. `short` is the tab-bar label — the full one
  // wraps to two lines in a 5-up bar on a 360px phone.
  const navItems = [
    { id: 'dashboard', icon: LayoutDashboard, label: 'Create clips', short: 'Create', primary: true },
    { id: 'settings', icon: Settings, label: 'Settings', short: 'Settings', primary: true },
  ];
  useEffect(() => {
    if (!['dashboard', 'library', 'settings'].includes(activeTab)) setActiveTab('dashboard');
  }, [activeTab]);

  const openLocalProject = async (projectJobId) => {
    const data = await pollJob(projectJobId);
    if (!data.result?.clips?.length) throw new Error('This project has no available clips.');
    flushClipState();
    if (projectJobId !== jobId) setProjectState(null);
    setJobId(projectJobId);
    setResults(data.result);
    setStatus(data.status === 'completed' ? 'complete' : data.status === 'failed' ? 'error' : 'processing');
    setLogs(data.logs || []);
    setLogTimes(data.log_times || []);
    setProcessingMedia(null);
    setNoSource(false);
    setJobError('');
    setQualityGate(null);
    setPartialJob(null);
    setFirstVideoJob(false);
    setQueueInfo(null);
    setSessionRecovered(false);
    setActiveTab('dashboard');
  };
  const activeNav = activeTab === 'library' ? { label: campaigns.filter === 'all' ? 'All videos' : campaigns.filter === 'unassigned' ? 'Unassigned' : campaigns.campaigns.find(c => c.id === campaigns.filter)?.name || 'Categories' } : navItems.find((n) => n.id === activeTab);

  // Escape closes the mobile drawer. The shell itself is overflow-hidden, so
  // there is no body scroll to lock behind it.
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e) => { if (e.key === 'Escape') setNavOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [navOpen]);

  const goToTab = (id) => {
    if (tutorialLock && id !== 'dashboard') return;
    setActiveTab(id);
    setNavOpen(false);
  };
  const tabLocked = (id) => tutorialLock && id !== 'dashboard';

  // Shared footer links (landing, repo, pricing, contact) — same list in the
  // desktop rail and the mobile drawer, so they can never drift apart.
  const NavFooterLinks = () => (
    <button type="button" onClick={() => goToTab('settings')} aria-current={activeTab === 'settings' ? 'page' : undefined} className={`local-nav-item w-full text-left ${activeTab === 'settings' ? 'is-active' : ''}`}><Settings size={18} /><span>Settings</span></button>
  );

  // Desktop rail: icon-only from md, labelled from lg. Below md it is gone
  // entirely — an unlabelled 80px rail ate a fifth of a phone screen.
  const Sidebar = () => (
    <div className="local-sidebar hidden md:flex flex-col h-full shrink-0">
      <a href="#app" onClick={() => goToTab('dashboard')} className="local-sidebar-brand p-6 flex items-center gap-3" title="CreateShorts Local Studio">
        <div className="w-8 h-8 bg-paper3 rounded-input flex items-center justify-center shrink-0 overflow-hidden border border-rule">
          <img src="/logo-openshorts.png" alt="Logo" className="w-full h-full object-cover" />
        </div>
        <span className="font-semibold text-lg leading-tight">CreateShorts<span className="block text-xs font-normal opacity-60 mt-1">Local Studio</span></span>
      </a>

      <nav className="px-4 py-4 space-y-1">
        {navItems.filter(item => item.id !== 'settings').map((item) => {
          const NavIcon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              data-tutorial={item.id === 'dashboard' ? 'nav-clips' : undefined}
              onClick={() => goToTab(item.id)}
              title={tabLocked(item.id) ? 'Finish your first clips to unlock' : item.label}
              disabled={tabLocked(item.id)}
              aria-current={isActive ? 'page' : undefined}
              className={`local-nav-item ${isActive ? 'is-active' : ''} relative w-full flex items-center gap-3 px-3 py-2.5 rounded-input transition-colors ${isActive ? 'bg-paper3 text-ink' : 'text-muted hover:text-ink2 hover:bg-paper3/50'} ${tabLocked(item.id) ? 'opacity-40 cursor-not-allowed hover:bg-transparent hover:text-muted' : ''}`}
            >
              {isActive && (
                <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 bg-brass rounded-full" aria-hidden="true" />
              )}
              <NavIcon size={18} className={`shrink-0 ${isActive ? 'text-brass' : ''}`} />
              <span className="text-sm flex-1 text-left truncate">{item.label}</span>
              {tabLocked(item.id)
                ? <Lock size={12} className="shrink-0 hidden lg:block" />
                : item.byok ? <span className="readout hidden lg:block">BYOK</span>
                  : item.isNew ? <span className="badge-brass hidden lg:block">new</span> : null}

            </button>
          );
        })}
      </nav>

      <div className="flex-1 overflow-y-auto custom-scrollbar"><CampaignNav active={activeTab === 'library'} onOpen={() => goToTab('library')} /></div>
      <div className="p-4 border-t border-rule space-y-1">
        <NavFooterLinks collapsed />
      </div>
    </div>
  );

  // Mobile drawer: the complete nav, reachable from the header's menu button.
  const MobileNavDrawer = () => (
    <div
      className="md:hidden fixed inset-0 z-[90] flex"
      role="dialog"
      aria-modal="true"
      aria-label="Navigation"
    >
      <div
        className="absolute inset-0 bg-black/60 animate-fade"
        onClick={() => setNavOpen(false)}
        aria-hidden="true"
      />
      <div className="relative w-[17rem] max-w-[82vw] h-full bg-paper2 border-r border-rule flex flex-col animate-slide-in-left">
        <div className="flex items-center justify-between px-5 h-14 border-b border-rule shrink-0">
          <a href="#app" className="flex items-center gap-2.5" onClick={() => goToTab('dashboard')}>
            <div className="w-7 h-7 bg-paper3 rounded-input overflow-hidden border border-rule shrink-0">
              <img src="/logo-openshorts.png" alt="" className="w-full h-full object-cover" />
            </div>
            <span className="font-display text-lg text-ink">CreateShorts</span>
          </a>
          <button
            onClick={() => setNavOpen(false)}
            aria-label="close navigation"
            className="p-2 -mr-2 text-muted hover:text-ink transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto custom-scrollbar px-3 py-3 space-y-1">
          {navItems.filter(item => item.id !== 'settings').map((item) => {
            const NavIcon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => goToTab(item.id)}
                disabled={tabLocked(item.id)}
                aria-current={isActive ? 'page' : undefined}
                title={tabLocked(item.id) ? 'Finish your first clips to unlock' : undefined}
                className={`relative w-full flex items-center gap-3 px-3 py-3 rounded-input transition-colors ${isActive ? 'bg-paper3 text-ink' : 'text-muted active:bg-paper3/60'} ${tabLocked(item.id) ? 'opacity-40 cursor-not-allowed' : ''}`}
              >
                {isActive && (
                  <span className="absolute left-0 top-2 bottom-2 w-0.5 bg-brass rounded-full" aria-hidden="true" />
                )}
                <NavIcon size={18} className={`shrink-0 ${isActive ? 'text-brass' : ''}`} />
                <span className="text-[0.95rem] lowercase flex-1 text-left truncate">{item.label}</span>
                {tabLocked(item.id)
                  ? <Lock size={12} className="shrink-0" />
                  : item.byok ? <span className="readout shrink-0">BYOK</span>
                    : item.isNew ? <span className="badge-brass shrink-0">new</span> : null}
              </button>
            );
          })}
          <CampaignNav active={activeTab === 'library'} onOpen={() => goToTab('library')} />
        </nav>

        <div className="px-3 py-3 border-t border-rule space-y-0.5 safe-bottom shrink-0">
          <NavFooterLinks />
        </div>
      </div>
    </div>
  );

  // Bottom tab bar: the four everyday destinations plus "more" for the rest.
  // It is a flex sibling of the scrolling pane rather than `fixed`, so nothing
  // ever hides behind it and no pane needs compensating padding.
  const MobileTabBar = () => (
    <nav aria-label="Main navigation" className="md:hidden shrink-0 border-t border-rule bg-paper2 safe-bottom">
      <div className="flex items-stretch">{navItems.map((item) => {
        const NavIcon = item.icon;
        const selected = activeTab === item.id;
        return <button key={item.id} onClick={() => goToTab(item.id)} aria-current={selected ? 'page' : undefined} className={`flex-1 flex flex-col items-center justify-center gap-1 py-3 min-h-[60px] text-xs font-medium ${selected ? 'text-brass' : 'text-muted'}`}><NavIcon size={20} /><span>{item.short}</span></button>;
      })}</div>
    </nav>
  );

  if (editorRoute) return <VideoEditorLoader key={`${editorRoute.targetId}/${editorRoute.clipIndex}`} {...editorRoute} ready={!authLoading}
    editorProps={{
      geminiApiKey: apiKey, elevenLabsKey, uploadPostKey, uploadUserId, isManaged,
      initialState: editorRoute.targetId === jobId ? projectState?.clips?.find(clip => clip.index === editorRoute.clipIndex) : null,
      onStateChange: editorRoute.targetId === jobId ? handleClipStateChange : undefined,
      connectedPlatforms: (userProfiles.find(profile => profile.username === uploadUserId) || userProfiles[0])?.connected ?? null,
    }}
    onVideoUpdated={(updatedJobId, index, data) => {
      if (updatedJobId === jobId) handleClipVideoUpdated(index, data);
      setLibraryRevision(value => value + 1);
    }}
    onClose={() => {
      if (!window.history.state?.openshortsEditor) setActiveTab('library');
      setLibraryRevision(value => value + 1);
      closeEditorRoute();
    }} />;

  return (
    /* h-dvh where supported: on mobile Safari/Chrome `100vh` is the tallest the
       viewport ever gets, so a h-screen shell hides its own bottom bar behind
       the browser chrome until the user scrolls. */
    <div className="local-studio flex h-screen supports-[height:100dvh]:h-[100dvh] bg-paper overflow-hidden">
      <Sidebar />
      {navOpen && <MobileNavDrawer />}

      <main className="local-main flex-1 min-w-0 flex flex-col h-full overflow-hidden relative">
        {/* Top Header */}
        <header className="local-topbar h-16 border-b border-rule bg-paper2 flex items-center justify-between gap-2 px-4 sm:px-8 shrink-0 z-10">
          <div className="flex items-center gap-2 sm:gap-4 min-w-0">
            {/* Mobile: the drawer handle, and the section name the icon rail
                used to carry. Without it a phone has no "where am I". */}
            <button
              onClick={() => setNavOpen(true)}
              aria-label="open navigation"
              className="md:hidden -ml-1 p-2 rounded-input text-muted active:bg-paper3 transition-colors shrink-0"
            >
              <Menu size={20} />
            </button>
            <span data-tutorial="nav-clips" className="text-sm font-semibold text-ink truncate">
              {activeNav?.label || 'CreateShorts'}
            </span>
            {status !== 'idle' && (
              <button
                onClick={status === 'processing' ? handleStop : handleReset}
                disabled={isStopping || (status === 'processing' && !jobId)}
                className="btn-quiet px-3 py-1.5 text-xs shrink-0"
                aria-label="New Project"
              >
                <Plus size={14} />
                <span className="hidden sm:inline">{isStopping ? 'Stopping…' : 'New Project'}</span>
              </button>
            )}
          </div>

          <div className="flex items-center gap-2 sm:gap-4 shrink-0">
            <span className="badge-ok"><span className="w-1.5 h-1.5 rounded-full bg-ok" /> Local Studio</span>
            {userProfiles.length > 0 && (
              <UserProfileSelector
                profiles={userProfiles}
                selectedUserId={uploadUserId}
                onSelect={setUploadUserId}
                onConnect={isManaged ? handleConnectSocials : undefined}
              />
            )}

            {/* Cloud: minutes meter + account/sign-in. For free users the meter
                opens the upgrade modal — otherwise the only path to a plan is
                failing against the quota wall. */}
            {billingEnabled && isManaged && (
              <UsageMeter onClick={() => {
                if (plan === 'free') openUpsell({ source: 'meter' });
                else { window.location.hash = '#/account'; }
              }} />
            )}
            {billingEnabled && isSignedIn && !isManaged && (
              <button onClick={() => setShowPlanChoice(true)}
                className="btn-primary px-4 py-2 text-xs">
                Choose a plan
              </button>
            )}
            {billingEnabled && !isSignedIn && (
              <button onClick={() => setShowLogin(true)}
                className="btn-ghost px-4 py-2 text-xs">
                Sign in
              </button>
            )}
            {billingEnabled && isSignedIn && <ProfileMenu onUpgrade={isManaged ? openUpsell : null} />}

            {keysMissing && <button onClick={() => goToTab('settings')} className="badge-warn"><AlertTriangle size={12} />Check analysis setup</button>}

          </div>
        </header>

        {keysMissing && activeTab !== 'settings' && <div role="status" className="mx-5 mt-4 card p-4 flex items-center gap-3 text-sm"><AlertTriangle className="text-warn shrink-0" size={18} /><p className="text-muted flex-1">{localLlm?.model === 'codex-account' ? 'Analysis is not available. Check that your Codex host adapter is running and signed in.' : 'Analysis is not available. Configure your AI provider using SETUP.md, then restart the backend and reload this page.'}</p><button className="btn-quiet" onClick={() => goToTab('settings')}>Settings</button></div>}

        {/* Session Recovery Banner */}
        {sessionRecovered && (
          <div className="mx-3 sm:mx-6 mt-2 px-3.5 sm:px-4 py-3 bg-paper2 border border-rule rounded-card flex items-start justify-between gap-3 animate-fade shrink-0">
            <div className="flex items-start sm:items-center gap-2 text-sm text-ink2 flex-wrap min-w-0">
              <RotateCcw size={16} className="text-brass shrink-0 mt-0.5 sm:mt-0" />
              <span className="font-medium">Session recovered</span>
              <span className="text-muted text-xs">Your previous work has been restored.</span>
            </div>
            <button
              onClick={() => setSessionRecovered(false)}
              aria-label="dismiss"
              className="text-muted hover:text-ink transition-colors shrink-0 -m-1 p-1"
            >
              <X size={16} />
            </button>
          </div>
        )}

        {/* The restored job is no longer on the server (purged or not yours). */}
        {jobGone && (
          <div role="status" className="mx-3 sm:mx-6 mt-2 px-3.5 sm:px-4 py-3 bg-paper2 border border-rule rounded-card flex items-start justify-between gap-3 animate-fade shrink-0">
            <div className="flex items-start sm:items-center gap-2 text-sm text-ink2 flex-wrap min-w-0">
              <History size={16} className="text-brass shrink-0 mt-0.5 sm:mt-0" />
              <span className="font-medium">That project is no longer open</span>
              <span className="text-muted text-xs">
                {billingEnabled && isSignedIn
                  ? 'If it finished, its clips are saved in History.'
                  : 'The server has already cleared it. Start a new project to keep going.'}
              </span>
            </div>
            <button
              onClick={() => setJobGone(false)}
              aria-label="dismiss"
              className="text-muted hover:text-ink transition-colors shrink-0 -m-1 p-1"
            >
              <X size={16} />
            </button>
          </div>
        )}

        {/* Included tools (Clip Generator, YouTube Studio): non-blocking trial prompt. */}
        {gateThisTab && <TrialGate toolName={TOOL_NAMES[activeTab] || 'this'} />}

        {/* Advanced tools (AI Shorts, AI Agent): BYOK fal.ai + ElevenLabs notice. */}
        {advancedThisTab && <AdvancedBanner needsPlan={needsPlan} onKeys={() => goToTab('settings')} />}

        {/* Main Workspace */}
        <div className="flex-1 overflow-hidden relative">

          {/* Local settings: only the services used by this installation. */}
          {activeTab === 'settings' && (
            <div className="local-workspace h-full overflow-y-auto custom-scrollbar p-5 sm:p-8">
              <div className="local-page-heading mb-7"><h1>Workspace settings</h1><p>Manage your analysis provider, transcription, and local exports.</p></div>
              <div className="max-w-3xl space-y-5">
                <section className="card p-6"><div className="flex items-center gap-3 mb-5"><Bot size={22} className="text-brass" /><h2 className="text-lg font-semibold text-ink">AI and video processing</h2></div>
                  <dl className="grid sm:grid-cols-2 gap-5 text-sm">
                    <div><dt className="text-muted mb-1">Moment selection</dt><dd className="font-medium text-ink">{localLlm?.model === 'codex-account' ? 'Your Codex account' : localLlm?.model || (apiKey ? 'Google Gemini' : 'Analysis provider not configured')}</dd></div>
                    <div><dt className="text-muted mb-1">Speech transcription</dt><dd className="font-medium text-ink">Whisper · on this computer</dd></div>
                    <div><dt className="text-muted mb-1">Video rendering</dt><dd className="font-medium text-ink">FFmpeg · on this computer</dd></div>
                    <div><dt className="text-muted mb-1">Publishing</dt><dd className="font-medium text-ink">Save clips locally and share them yourself</dd></div>
                  </dl>
                  <p className="mt-5 pt-4 border-t border-rule text-sm text-muted leading-relaxed">{localLlm?.model === 'codex-account' ? 'Your configured Codex host adapter receives the transcript to choose clips. Your video is processed locally. Normal Codex account usage limits apply.' : 'Your configured AI provider receives the transcript to choose clips. Whisper and FFmpeg process your video on this computer. See SETUP.md to configure the provider.'}</p>
                </section>
                <section className="card p-6"><h2 className="text-lg font-semibold text-ink mb-2">Connect an AI assistant</h2><p className="text-sm text-muted mb-4">Add this local MCP endpoint in Codex or another compatible assistant to upload videos, create clips, or adjust captions. Configure the connection in your assistant first.</p><div className="rounded-input bg-paper p-4 text-sm font-mono break-all">http://127.0.0.1:8000/mcp</div><p className="mt-4 text-sm text-muted">Keep Docker running while you work. Follow SETUP.md to start and stop the local services.</p></section>
              </div>
            </div>
          )}
          {activeTab === 'library' && <LocalLibrary onOpenProject={openLocalProject} onEdit={(project, kind) => setLibraryEditor({jobId: project.job_id, index: project.clip_index, title: project.title, kind})} editorProps={{geminiApiKey: apiKey, elevenLabsKey}} refreshKey={`${jobId}-${status}-${libraryRevision}`} />}

          {/* Create a new local project. */}
          {activeTab === 'dashboard' && status === 'idle' && (
            <div className="local-workspace h-full overflow-y-auto custom-scrollbar p-5 sm:p-8">
              <div className="local-page-heading mb-7"><p className="eyebrow mb-2">YOUR EDITING WORKSPACE</p><h1>Turn a long video into great shorts.</h1><p>Choose a source. Your AI provider finds the moments, and this computer creates the clips.</p></div>
              <div className="local-create-grid">
                <div className="min-w-0"><MediaInput onProcess={handleProcess} isProcessing={status === 'processing'} /></div>
                <aside className="min-w-0 space-y-5">
                  <section className="card p-5"><h2 className="font-semibold text-ink mb-4">From source to short</h2><ol className="space-y-4 text-sm">{[['1','Add your video','Upload a file or paste a video link.'],['2','Find the best moments','Your AI provider picks clips with a clear story.'],['3','Preview and fine-tune','Preview, style captions, and save your clips.']].map(([n,title,copy])=><li key={n} className="flex gap-3"><span className="w-7 h-7 rounded-full bg-brass/10 text-brass flex items-center justify-center font-semibold shrink-0">{n}</span><div><p className="font-medium text-ink">{title}</p><p className="text-muted mt-1 leading-relaxed">{copy}</p></div></li>)}</ol></section>
                  <LocalLibrary compact onOpenProject={openLocalProject} refreshKey={`${jobId}-${status}`} />
                </aside>
              </div>
            </div>
          )}

          {/* View: Processing / Results (Split View) */}
          {activeTab === 'dashboard' && (status === 'processing' || status === 'complete' || status === 'error') && (
            <div className="local-workspace local-results-workspace h-full overflow-y-auto custom-scrollbar p-5 sm:p-8 space-y-6">

              {status !== 'complete' && <section className="local-session-panel card p-5">
                <div className="flex flex-wrap items-center gap-3 justify-between"><div className="flex items-center gap-3"><Activity size={21} className="text-brass" /><div><h2 className="font-semibold text-ink">{status === 'complete' ? 'Your clips are ready' : status === 'error' ? 'This project needs attention' : 'Creating your shorts'}</h2><p className="text-sm text-muted mt-1">{status === 'complete' ? 'Preview each short, adjust its text, and save it to Clips.' : 'Transcribing, selecting moments, and rendering your clips.'}</p></div></div><span className={status === 'complete' ? 'badge-ok' : status === 'error' ? 'badge-danger' : 'badge-brass'}>{status === 'complete' ? 'Complete' : status === 'error' ? 'Failed' : 'In progress'}</span></div>
                {status === 'error' && <p role="alert" className="mt-4 text-sm text-danger">{jobError || 'The video could not be processed. Check the details below and try again.'}</p>}
                {status === 'processing' && <p role="status" className="mt-4 flex items-center gap-2 text-sm text-muted break-words"><Loader2 size={16} className="animate-spin shrink-0 text-brass" />{queueInfo ? `Waiting to start · ${queueInfo.ahead || 0} project(s) ahead` : processingMessage(logs)}</p>}
                {status === 'processing' && <div className="mt-4 flex flex-wrap items-center gap-3"><button type="button" onClick={handleStop} disabled={isStopping || !jobId} className="btn-quiet text-danger"><X size={16} />{isStopping ? 'Stopping…' : 'Stop creating clips'}</button><p className="text-xs text-muted">Stop this project and start a new video.</p></div>}
                {stopError && <p role="alert" className="mt-3 text-sm text-danger">{stopError}</p>}
                {processingMedia && <details className="mt-4 border-t border-rule pt-3"><summary className="text-sm font-medium text-muted cursor-pointer">Source video</summary><div className="mt-4 max-w-xl"><ProcessingAnimation media={processingMedia} isComplete={status === 'complete'} syncedTime={syncedTime} isSyncedPlaying={isSyncedPlaying} syncTrigger={syncTrigger} /></div></details>}
              </section>}

              {/* Right Panel: Results Grid */}
              <section className="w-full min-w-0 local-results-panel">
                {/* Title + counters on one row, the two actions on their own row
                    below. Wrapping them all together dropped a lone half-width
                    "schedule week" pill under the title on a phone. */}
                <div className="local-results-header mb-3 shrink-0">
                  <h2 className="font-display lowercase text-lg sm:text-xl text-ink flex flex-wrap items-center gap-2">
                    <span className="mr-auto">Generated shorts</span>
                    {rankedClips.length > 0 && (
                      <span className="readout bg-paper3 px-2.5 py-1 rounded-full">
                        {rankedClips.length} Clips
                      </span>
                    )}
                    {billingEnabled && results?.cost_analysis && !isManaged && (
                      <span className="readout bg-paper3 px-2.5 py-1 rounded-full" title={`Input: ${results.cost_analysis.input_tokens} | Output: ${results.cost_analysis.output_tokens}`}>
                        GEMINI · ${results.cost_analysis.total_cost.toFixed(5)}
                      </span>
                    )}
                  </h2>
                  {rankedClips.length > 0 && status === 'complete' && (
                    <div className="flex flex-col sm:flex-row sm:justify-end items-stretch sm:items-center gap-2">
                      <button
                        onClick={handleDownloadAll}
                        disabled={downloadingAll}
                        className="btn-ghost px-3 py-2 text-xs"
                        title="Download all clips as a ZIP"
                      >
                        {downloadingAll
                          ? <><Loader2 size={14} className="animate-spin" />zipping…</>
                          : <><Download size={14} />Download all</>}
                      </button>
                      {billingEnabled && rankedClips.length > 1 && (
                        <button
                          onClick={() => setShowScheduleWeek(true)}
                          className="btn-primary px-4 py-2 text-xs"
                        >
                          <Calendar size={14} />
                          schedule week
                        </button>
                      )}
                    </div>
                  )}
                </div>



                {status === 'complete' && jobRetentionSeconds > 0 && <p className="text-xs text-muted mb-4">Available for {formatRetention(jobRetentionSeconds)}. Download your clips to keep them.</p>}

                <div className="min-w-0">
                  {rankedClips.length > 0 ? (
                    <div className="local-results-grid pb-8">
                      {rankedClips.map(({ clip, index: i }) => (
                        <ResultCard
                          key={`${jobId}-${i}-${clip._aiEditCardKey ?? `${clip.start}-${clip.end}-${JSON.stringify(clip.recipe || null)}`}`}
                          clip={clip}
                          index={i}
                          jobId={jobId}
                          generationPending={status === 'processing'}
                          onEditClip={(index) => setEditingClip(index)}
                          onReframeClip={(index) => setReframingClip(index)}
                          onUpgrade={isManaged ? openUpsell : null}
                          initialState={projectState?.clips?.find((c) => c.index === i) || null}
                          onStateChange={handleClipStateChange}
                          onVideoUpdated={handleClipVideoUpdated}
                          durable={durableClips[i]}
                          uploadPostKey={uploadPostKey}
                          uploadUserId={uploadUserId}
                          geminiApiKey={apiKey}
                          elevenLabsKey={elevenLabsKey}
                          isManaged={isManaged}
                          connectedPlatforms={(userProfiles.find((p) => p.username === uploadUserId) || userProfiles[0])?.connected ?? null}
                          onConnectSocials={isManaged ? handleConnectSocials : null}
                          onPlay={(time) => handleClipPlay(time)}
                          onPause={handleClipPause}
                          onBulkSubtitle={handleBulkSubtitles}
                          clipCount={rankedClips.length}
                          bulkProgress={bulkSub}
                        />
                      ))}
                    </div>
                  ) : (
                    status === 'processing' ? (
                      <div className="h-full min-h-[140px] flex flex-col items-center justify-center text-muted space-y-3 text-center px-4">
                        <Loader2 size={28} className="animate-spin text-brass" />
                        <p className="text-sm lowercase">Waiting for clips...</p>
                        <p className="text-xs text-muted/80 max-w-[26ch] leading-snug">
                          They appear here one by one as each finishes rendering.
                        </p>
                      </div>
                    ) : status === 'error' ? (
                      <div className="h-full min-h-[120px] flex flex-col items-center justify-center text-danger space-y-2">
                        <p>Generation failed.</p>
                      </div>
                    ) : status === 'complete' ? (
                      <div className="card p-8 text-center text-muted text-sm">No clips remain in this project.</div>
                    ) : null
                  )}
                </div>
              </section>

            </div>
          )}

        </div>

        {/* Phone navigation. A flex sibling of the scrolling pane, not a fixed
            overlay, so content is never trapped behind it. */}
        <MobileTabBar />

      </main>

      <Modal isOpen={showKeyModal} onClose={() => setShowKeyModal(false)} title="Analysis is unavailable" footer={<button className="btn-primary" onClick={() => {setShowKeyModal(false);goToTab('settings');}}>View settings</button>}><p className="text-sm text-muted">{localLlm?.model === 'codex-account' ? 'Check that your Codex host adapter is running and signed in. Then reload this page and try again.' : 'Configure your AI provider using SETUP.md, restart the backend, then reload this page and try again.'}</p></Modal>

      {/* Pre-flight quality gate */}
      {qualityGate && (
        <Modal isOpen={true} onClose={() => setQualityGate(null)} size="md" eyebrow="HEADS UP" title="low source quality">
          <div className="space-y-4">
            <p className="text-sm text-ink2">
              YouTube only offers <span className="text-brass font-semibold">{qualityGate.info.max_height}p</span> for this video
              (below the {qualityGate.info.min_height}p we recommend). Processing anyway will produce lower-quality clips.
            </p>
            {qualityGate.info.cookies_invalid && (
              <p className="text-xs text-muted">
                Your YouTube cookies look expired — refreshing them (export again from an incognito window) often unlocks HD.
              </p>
            )}
            <div className="flex gap-2 justify-end pt-2">
              <button onClick={() => setQualityGate(null)} className="btn-ghost">cancel</button>
              <button
                onClick={() => { const d = qualityGate.data; setQualityGate(null); handleProcess(d, true); }}
                className="btn-primary"
              >
                process anyway
              </button>
            </div>
          </div>
        </Modal>
      )}


      {libraryEditor && (libraryEditor.kind === 'edit' ? <ClipEditor jobId={libraryEditor.jobId} clipIndex={libraryEditor.index} clipTitle={libraryEditor.title} onClose={() => setLibraryEditor(null)} onRerendered={() => {setLibraryRevision(v => v+1);setLibraryEditor(null);}} /> : <ReframeEditor jobId={libraryEditor.jobId} clipIndex={libraryEditor.index} clipTitle={libraryEditor.title} onClose={() => setLibraryEditor(null)} onReframed={() => {setLibraryRevision(v => v+1);setLibraryEditor(null);}} />)}
      {editingClip !== null && rankedClips.some(({ index }) => index === editingClip) && (
        <ClipEditor
          jobId={jobId}
          clipIndex={editingClip}
          clipTitle={rankedClips.find(({ index }) => index === editingClip)?.clip?.video_title_for_youtube_short || ''}
          onClose={() => setEditingClip(null)}
          onRerendered={handleClipRerendered}
        />
      )}
      {reframingClip !== null && rankedClips.some(({ index }) => index === reframingClip) && (
        <ReframeEditor
          jobId={jobId}
          clipIndex={reframingClip}
          clipTitle={rankedClips.find(({ index }) => index === reframingClip)?.clip?.video_title_for_youtube_short || ''}
          onClose={() => setReframingClip(null)}
          onReframed={handleClipRerendered}
        />
      )}

    </div>
  );
}

export default App;
