// Saved copies keep their file identity in Saved, but share their source's
// identity in All videos. Never merge unrelated clips just by their title.
export function uniqueLibraryVideos(videos) {
    const positions = new Map();
    const result = [];
    for (const video of videos) {
        const key = video.source_key || (video.local_file_id ? `file/${video.local_file_id}`
            : video.clip && video.clip_index != null ? `${video.job_id}/clip/${video.clip_index}` : `project/${video.job_id}`);
        if (!positions.has(key)) {
            positions.set(key, result.length);
            result.push(video);
            continue;
        }
        const index = positions.get(key);
        const previous = result[index];
        // Open the current editable project rather than an exported snapshot.
        const preferred = !video.local_file_id && video.clip ? video : previous;
        result[index] = { ...preferred, saved: Boolean(previous.saved || video.saved) };
    }
    return result;
}
