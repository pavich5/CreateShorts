export const VIDEO_REMOVED_EVENT = 'os:video-removed';

// Saved copies and their source card share one deletion, even across views.
export function isRemovedVideo(removed, { jobId, clipIndex, fileId, sourceKey } = {}) {
    if (!removed) return false;
    const files = removed.file_ids || [];
    const clips = removed.clip_ids || [];
    if (fileId && files.includes(fileId)) return true;
    if (jobId && (removed.job_ids || []).includes(jobId)) return true;
    if (jobId && clips.some(clip => clip.job_id === jobId && Number(clip.clip_index) === Number(clipIndex))) return true;
    if (sourceKey) {
        if (files.some(id => sourceKey === `file/${id}`)) return true;
        if (clips.some(clip => sourceKey === `${clip.job_id}/clip/${clip.clip_index}`)) return true;
    }
    return false;
}
