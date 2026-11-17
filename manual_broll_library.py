"""Create a local editable draft for an existing Clips-library video."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from fastapi import HTTPException

from broll import _probe_video
from jsonfile import write_json_atomic

_import_lock = threading.Lock()


def _adopt_ai_draft(namespace, file_id, input_filename, job_id, row):
    """Continue the explicitly selected local AI draft in the media editor."""
    if not isinstance(input_filename, str) or not re.fullmatch(r"ai_[0-9a-f]{32}\.mp4", input_filename):
        return
    output_root = Path(namespace["OUTPUT_DIR"]).resolve()
    ai_root = output_root / ("ai_" + file_id)
    source = ai_root / input_filename
    if (ai_root.is_symlink() or ai_root.resolve().parent != output_root
            or source.is_symlink() or not source.is_file()):
        return
    output = output_root / job_id
    if output.is_symlink():
        raise HTTPException(status_code=400, detail="Invalid draft directory")
    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / (job_id + "_metadata.json")
    if metadata_path.is_symlink():
        raise HTTPException(status_code=400, detail="Invalid draft files")
    if metadata_path.is_file():
        try:
            previous = json.loads(metadata_path.read_text())
        except (ValueError, OSError):
            raise HTTPException(status_code=400, detail="This draft could not be read.") from None
        if previous.get("library_input_filename") == input_filename:
            return
    info = _probe_video(str(source))
    imported = output / ("manual_import_" + input_filename)
    temporary = output / ("temp_import_" + uuid.uuid4().hex + ".mp4")
    try:
        shutil.copyfile(source, temporary)
        temporary.replace(imported)
        sidecar = Path(str(source) + ".layout.json")
        if sidecar.is_file() and not sidecar.is_symlink():
            shutil.copyfile(sidecar, str(imported) + ".layout.json")
        title = str(row.get("title") or source.stem)
        clip = {"start": 0.0, "end": info["duration"], "duration_seconds": info["duration"],
                "video_title_for_youtube_short": title,
                "video_url": f"/videos/{job_id}/{imported.name}"}
        write_json_atomic(str(metadata_path), {"title": title, "shorts": [clip],
            "transcript": {"segments": []}, "source_video": imported.name,
            "library_source_id": file_id, "library_input_filename": input_filename}, indent=2)
        namespace["jobs"].pop(job_id, None)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_library_job(namespace, file_id, input_filename=None):
    """Return a stable draft job; never overwrite the library's source file."""
    if namespace["BILLING_ENABLED"]:
        raise HTTPException(status_code=404, detail="Not found")
    if not isinstance(file_id, str) or not re.fullmatch(r"[0-9a-f]{64}", file_id):
        raise HTTPException(status_code=404, detail="Video not found")
    job_id = "manual_" + file_id
    with _import_lock:
        store = namespace.get("LOCAL_CAMPAIGNS")
        if store is not None:
            with store.lock:
                if file_id in (store.snapshot().get("video_removals") or {}):
                    raise HTTPException(status_code=404, detail="Video has been removed")
        from video_removal import read_deleted_indices
        if 0 in read_deleted_indices(Path(namespace["OUTPUT_DIR"]) / job_id):
            raise HTTPException(status_code=404, detail="Video has been removed")
        try:
            source, row = namespace["LOCAL_FOLDER_LIBRARY"].locate(file_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="This video is no longer in Clips.") from exc
        source = Path(source)
        if not source.is_file():
            raise HTTPException(status_code=404, detail="Video not found")
        _adopt_ai_draft(namespace, file_id, input_filename, job_id, row)
        if job_id in namespace["jobs"]:
            return job_id
        output = Path(namespace["OUTPUT_DIR"]) / job_id
        if output.is_symlink():
            raise HTTPException(status_code=400, detail="Invalid draft directory")
        output.mkdir(parents=True, exist_ok=True)
        metadata_path = output / (job_id + "_metadata.json")
        canonical = output / (job_id + "_clip_1.mp4")
        if metadata_path.is_symlink() or canonical.is_symlink():
            raise HTTPException(status_code=400, detail="Invalid draft files")
        if not metadata_path.is_file():
            temporary = output / ("temp_import_" + uuid.uuid4().hex + ".mp4")
            try:
                if source.suffix.lower() == ".mp4":
                    shutil.copyfile(source, temporary)
                else:
                    # Remux first; only codecs the MP4 container cannot store
                    # need a fresh local encode. No provider or ASR is involved.
                    command = ["ffmpeg", "-y", "-v", "error", "-i", str(source),
                               "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
                               "-movflags", "+faststart", str(temporary)]
                    result = subprocess.run(command, capture_output=True, timeout=300)
                    if result.returncode:
                        from ffmpeg_utils import QUALITY_FAST, video_encode_args
                        command = command[:command.index("-c")] + [
                            *video_encode_args(QUALITY_FAST), "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-movflags", "+faststart", str(temporary)]
                        subprocess.run(command, capture_output=True, timeout=300, check=True)
                info = _probe_video(str(temporary))
                temporary.replace(canonical)
                title = str(row.get("title") or source.stem)
                clip = {"start": 0.0, "end": info["duration"],
                        "duration_seconds": info["duration"],
                        "video_title_for_youtube_short": title,
                        "video_url": f"/videos/{job_id}/{canonical.name}"}
                write_json_atomic(str(metadata_path), {"title": title, "shorts": [clip],
                                  "transcript": {"segments": []},
                                  "source_video": canonical.name,
                                  "library_source_id": file_id}, indent=2)
            except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
                raise HTTPException(status_code=400, detail="This video could not be opened for editing.") from exc
            finally:
                temporary.unlink(missing_ok=True)
        try:
            metadata = json.loads(metadata_path.read_text())
            clips = metadata["shorts"]
            if not isinstance(clips, list) or not clips:
                raise ValueError("Missing draft clip")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="This draft could not be read.") from exc
        namespace["jobs"][job_id] = {
            "status": "completed", "result": {"clips": clips, "title": metadata.get("title")},
            "output_dir": str(output), "base_name": job_id, "user_id": None,
            "watermark": False, "submitted_at": time.time(),
            "logs": namespace.get("_TimedLog", list)(),
        }
        return job_id
