export function processingMessage(logs = []) {
  for (let i=logs.length-1;i>=0;i--) {
    const line=String(logs[i]);
    if (/torch\.|numpy|tensor\s*=|File "|Traceback|Warning:|\.py:\d+|^\s*(?:def |return |import )/.test(line)) continue;
    if (/process finished successfully|delivery.*complete/i.test(line)) return 'Finishing your clips…';
    if (/\[Codex editor\]/i.test(line)) return 'Choosing the hook and visual moments…';
    if (/\[B-roll\]/i.test(line)) {
      if (/skipped|failed|unavailable|no suitable|without/i.test(line)) return 'Creating your video clips…';
      return 'Adding visual B-roll…';
    }
    if (/burning|captions burned|hook.*burn|rendering|rendered|encoding|frame=|clip ready/i.test(line)) return 'Creating your video clips…';
    if (/found.*clips|selected.*moments|scoring|analyzing|analysing|selecting|shortlist/i.test(line)) return 'Choosing the best moments…';
    if (/transcrib|recognizing speech/i.test(line)) return 'Transcribing the speech…';
    if (/download|fetching video/i.test(line)) return 'Preparing your source video…';
    if (/queued/i.test(line)) return 'Waiting to start…';
  }
  return 'Processing your video…';
}
