"""Inspect real outputs after Playwright; run inside the backend container.

python dashboard/e2e/verify_exports.py artifacts/e2e-2026-11-13
No media is substituted and no render is mocked.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ]))


def inspect(path):
    info = probe(path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert video["width"] * 16 == video["height"] * 9, (path, "not 9:16")
    assert video["codec_name"] == "h264", (path, "unexpected codec")
    assert audio, (path, "missing audio")
    decoded = subprocess.run([
        "ffmpeg", "-hide_banner", "-v", "info", "-xerror", "-err_detect", "explode", "-i", str(path),
        "-vf", "blackdetect=d=0.08:pix_th=0.10,freezedetect=n=-60dB:d=2", "-f", "null", "-"
    ], capture_output=True, text=True, timeout=180)
    assert decoded.returncode == 0, decoded.stderr[-1500:]
    findings = [line for line in decoded.stderr.splitlines() if "black_start:" in line or "freeze_" in line]
    return {
        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "video_duration": float(video["duration"]), "container_duration": float(info["format"]["duration"]),
        "width": video["width"], "height": video["height"], "fps": video["avg_frame_rate"],
        "codec": video["codec_name"], "audio": [{k: s.get(k) for k in ("codec_name", "sample_rate", "channels", "duration")} for s in audio],
        "decoded_without_errors": True, "black_freeze_findings": findings,
    }


def main(directory):
    job = json.loads((directory / "job.json").read_text())["jobId"]
    root = Path("output") / job
    metadata = json.loads(next(root.glob("*_metadata.json")).read_text())
    clips = []
    hashes = {}
    for index, clip in enumerate(metadata["shorts"]):
        if clip.get("deleted"):
            continue
        media = root / clip["video_url"].split("/")[-1]
        result = inspect(media)
        result.update(index=index, stored_duration=clip.get("duration_seconds"), recipe=clip.get("recipe"))
        assert abs(result["container_duration"] - clip["duration_seconds"]) < .06, result
        hashes[result["sha256"]] = index
        clips.append(result)
    exports = []
    manifest = directory / "browser-exports.json"
    assert manifest.is_file(), "Run the real browser exports first; no export evidence is available"
    saved = json.loads(manifest.read_text())
    assert len(saved) >= 2, "The repeated export workflow must produce at least two files"
    for item in saved:
        relative = item["saved_path"].removeprefix("Documents/Clips/")
        source_key = item.get("expected_source_key") or item.get("store", {}).get("editable_sources", {}).get(relative)
        assert source_key and source_key.startswith(job + "/clip/"), (relative, "missing export source identity")
        expected_index = int(source_key.rsplit("/", 1)[-1])
        result = inspect(Path(os.environ.get("CLIPS_LIBRARY_DIR", "/clips")) / relative)
        result["expected_clip_index"] = expected_index
        result["matches_current_clip_index"] = hashes.get(result["sha256"])
        assert result["matches_current_clip_index"] == expected_index, "Export differs from its intended clip preview"
        exports.append(result)
    output = {"job_id": job, "clips": clips, "exports": exports,
              "note": "Freeze detections need source comparison; they can reflect intentional still footage. ASR semantics require listening."}
    (directory / "video-export-verification.json").write_text(json.dumps(output, indent=2))
    print(json.dumps({"clips": len(clips), "exports": len(exports), "all_9_16": True, "decoded": True}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
