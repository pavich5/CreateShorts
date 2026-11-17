"""Authenticated manual image placement endpoints for existing clip projects."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

import manual_broll as manual
from clip_identity import find_clip_by_index


def install_manual_broll_routes(app, runtime):
    """Bind existing app ownership, delivery, and persistence helpers lazily."""
    router = APIRouter(prefix="/api/clip/{job_id}/{clip_index}/broll")

    async def resolve_job_id(job_id, input_filename=None):
        if job_id.startswith("file:"):
            from manual_broll_library import materialize_library_job
            if input_filename:
                lock = runtime["_rerender_locks"].setdefault("manual_" + job_id[5:], asyncio.Lock())
                async with lock:
                    return await asyncio.to_thread(materialize_library_job, runtime, job_id[5:], input_filename)
            return await asyncio.to_thread(materialize_library_job, runtime, job_id[5:])
        return job_id

    def copy_layout(source, target):
        sidecar = Path(str(source) + ".layout.json")
        if sidecar.is_file():
            shutil.copyfile(sidecar, str(target) + ".layout.json")

    def safe_file(root, name):
        if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith(".mp4"):
            raise HTTPException(409, "The editable video is unavailable. Reopen the project and try again.")
        path = root / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            raise HTTPException(409, "The editable video is unavailable. Reopen the project and try again.")
        return path

    def filename(url):
        return Path(unquote(urlsplit(str(url or "")).path)).name

    async def load(request, job_id, clip_index, *, editing=False):
        job_id = await resolve_job_id(job_id, request.query_params.get("input_filename") if not editing else None)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", job_id) or clip_index < 0:
            raise HTTPException(404, "Clip not found")
        if editing:
            await runtime["require_managed_entitlement"](request)
        await runtime["_ensure_job_files"](job_id, request)
        job = runtime["jobs"].get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        await runtime["_assert_job_owner"](request, job)
        output = Path(runtime["OUTPUT_DIR"]).resolve()
        root = output / job_id
        if root.is_symlink() or root.resolve().parent != output or not root.is_dir():
            raise HTTPException(404, "Project files not found")
        paths = sorted(root.glob("*_metadata.json"))
        if not paths or paths[0].is_symlink():
            raise HTTPException(404, "Project metadata not found")
        data = await asyncio.to_thread(lambda: json.loads(paths[0].read_text()))
        clips = data.get("shorts") or []
        if clip_index >= len(clips):
            raise HTTPException(404, "Clip not found")
        clip = clips[clip_index]
        if clip.get("deleted"):
            raise HTTPException(404, "Video has been removed")
        served = filename(clip.get("video_url"))
        if not served:
            served = runtime["_canonical_clip_file"](str(root), paths[0].name.removesuffix("_metadata.json"), clip_index)
        safe_file(root, served)
        return job_id, job, root, paths[0], data, clip, served

    def resolve_base(root, clip, served):
        clean = runtime["_strip_watermark_copy"](str(root), served)
        had_captions, hook_layer = False, None
        # Captions below media produce hooked_subtitled_broll_base, while
        # captions above it produce subtitled_hooked_broll_base. Unwrap each
        # known text layer in its actual order before resolving the media base.
        while True:
            if re.match(r"^subtitled_\d+_", clean):
                under = runtime["_strip_burned_captions"](str(root), clean, materialize=False)
                if under == clean:
                    raise HTTPException(409, "The video without burned captions is unavailable; restore its project files first.")
                had_captions = had_captions or clip.get("caption_words") != []
            elif re.match(r"^(hooked_\d+_|hook_)", clean):
                hook_layer = hook_layer or clean
                under = runtime["_strip_burned_hook"](str(root), clean)
                if under == clean:
                    raise HTTPException(409, "The video without its hook is unavailable; restore its project files first.")
            else:
                break
            clean = under
        record = clip.get("broll") or {}
        if record.get("mode") == "manual" and record.get("base_file"):
            # A recut refreshes this base via broll_edit. Caption/hook restyles
            # keep it, so a second save always replaces earlier placements.
            if clean == record.get("file") or not record.get("cues"):
                clean = record["base_file"]
        # A saved manual base may itself contain earlier generated cutaways.
        # Those pixels are part of the base, not a layer to strip on undo.
        while not (record.get("mode") == "manual" and clean == record.get("base_file")):
            match = re.match(r"^broll_\d+_(.+)$", clean)
            if match is None:
                break
            safe_file(root, match.group(1))
            clean = match.group(1)
        if record.get("mode") == "manual" and record.get("cues") and clean != record.get("base_file"):
            # Do not silently discard a later edit that baked these images
            # into new pixels. Ordinary cuts/reframes update base_file.
            if record.get("file") and record["file"] in clean:
                raise HTTPException(409, "This video has a later visual edit. Remove or finish that edit before replacing its images.")
        return safe_file(root, clean), had_captions, hook_layer

    def overlay_fingerprint(clip, base):
        return hashlib.sha256(json.dumps({"base": base.name,
            **{key: clip.get(key) for key in ("auto_hook", "caption_style", "caption_words", "recipe", "start", "end")}},
            sort_keys=True).encode()).hexdigest()

    def restore_overlays(root, clip, data, picture, had_captions, hook_layer):
        import hooks
        import layout_ranges
        import recut
        path = str(picture)
        hook = clip.get("auto_hook")
        if hook_layer:
            recipe_file, image_file = hooks.recipe_paths(str(root), hook_layer)
            if (Path(recipe_file).is_file() and not Path(recipe_file).is_symlink()
                    and Path(recipe_file).stat().st_size < 16384
                    and Path(image_file).is_file() and not Path(image_file).is_symlink()):
                recipe = json.loads(Path(recipe_file).read_text())
                out = root / f"hooked_{time.time_ns()}_{Path(path).name}"
                hooks._write_recipe(str(out), path, image_file,
                                    recipe["x"], recipe["y"], recipe.get("duration"))
                if not hooks.materialize(str(root), out.name):
                    raise RuntimeError("Hook could not be preserved")
                copy_layout(path, out)
                path = str(out)
            elif isinstance(hook, dict) and hook.get("text"):
                out = root / f"hooked_{time.time_ns()}_{Path(path).name}"
                runtime["add_hook_to_video"](path, hook["text"], str(out),
                    position=hook.get("position") or "top", duration=hook.get("duration_seconds", 5),
                    style=hook.get("style") or "pill", font=hook.get("font"), has_captions=had_captions,
                    layout_ranges=layout_ranges.read(path))
                path = str(out)
            else:
                raise RuntimeError("Hook could not be preserved")
        if had_captions:
            transcript = data.get("transcript") or {"segments": [], "language": "en"}
            segments = (clip.get("recipe") or {}).get("segments")
            if segments:
                transcript = recut.virtual_transcript(transcript, segments)
                start, end = 0.0, recut.total_duration(segments)
            else:
                start, end = float(clip.get("start", 0)), float(clip.get("end", 0))
            if clip.get("caption_words"):
                transcript = runtime["_words_transcript"](clip["caption_words"], transcript.get("language", "en"))
                start, end = 0.0, float(clip.get("duration_seconds") or (end - start))
            if clip.get("caption_style"):
                captioned = runtime["_restyle_captions"](path, transcript, start, end,
                                                       clip["caption_style"], layout_ranges.read(path))
            else:
                import main
                captioned = main.auto_caption_clip(path, transcript, start, end,
                    split_ranges=layout_ranges.split_ranges(layout_ranges.read(path)))
            if not captioned:
                raise RuntimeError("Captions could not be preserved")
            path = captioned
        return Path(path)

    def image_url(job_id, asset):
        return runtime["_signed_media_url"](f"/videos/{job_id}/{asset}")

    def preview_captions(clip, data):
        import recut
        transcript = data.get("transcript") or {"segments": []}
        segments = (clip.get("recipe") or {}).get("segments")
        start = float(clip.get("start", 0))
        if segments:
            transcript = recut.virtual_transcript(transcript, segments)
            start = 0
        if isinstance(clip.get("caption_words"), list):
            words = clip["caption_words"]
            start = 0
        else:
            words = [{**word, "utteranceId": index} for index, segment in enumerate(transcript.get("segments", []))
                     for word in segment.get("words", [])]
        duration = float(clip.get("duration_seconds") or (float(clip.get("end", 0)) - float(clip.get("start", 0))))
        from subtitles import merge_untimed_caption_words
        selected = [{**word, "start": max(0, float(word["start"]) - start),
                     "end": min(duration, float(word["end"]) - start)}
                    for word in words if (float(word["end"]) > start or
                        float(word["start"]) == float(word["end"]) == start)
                    and float(word["start"]) < start + duration]
        return [{"text": str(word.get("word", "")).strip(),
                 "startMs": round(word["start"] * 1000),
                 "endMs": round(word["end"] * 1000), "utteranceId": word.get("utteranceId", 0)}
                for word in merge_untimed_caption_words(selected)]

    def base_preview(root, clip_index, clip, data, base, had_captions, hook_layer):
        """Cache an image-free styled preview without confusing clip recovery."""
        if not had_captions and not hook_layer:
            return base.name
        fingerprint = hashlib.sha256(f"{overlay_fingerprint(clip, base)}:{had_captions}:{hook_layer}".encode()).hexdigest()
        preview = root / f"manual_preview_{fingerprint[:16]}_clip_{clip_index + 1}.mp4"
        if preview.is_file() and not preview.is_symlink():
            return preview.name
        token = uuid.uuid4().hex
        temporary = root / f"manual_base_{token}_clip_{clip_index + 1}.mp4"
        try:
            shutil.copyfile(base, temporary)
            copy_layout(base, temporary)
            styled = restore_overlays(root, clip, data, temporary, had_captions, hook_layer)
            copy_layout(styled, preview)
            styled.replace(preview)
            return preview.name
        finally:
            # All scratch names contain our unique token; none can be an
            # original clip, and the cached preview itself does not contain it.
            for artifact in root.iterdir():
                if token in artifact.name and artifact.is_file():
                    artifact.unlink(missing_ok=True)

    def revision(clip):
        return hashlib.sha256(json.dumps(clip, sort_keys=True).encode()).hexdigest()

    def public_placements(job_id, record):
        return [{**item, "media_url": image_url(job_id, item["asset"]),
                 "image_url": image_url(job_id, item["asset"]),
                 "source_start": item.get("media_start", 0)}
                for item in manual.displayed_placements(record)]

    @router.get("")
    async def get_broll(job_id: str, clip_index: int, request: Request):
        job_id, _, root, _, data, clip, served = await load(request, job_id, clip_index)
        import broll
        base, had_captions, hook_layer = await asyncio.to_thread(resolve_base, root, clip, served)
        info = await asyncio.to_thread(broll._probe_video, str(base))
        duration = info["duration"]
        record = clip.get("broll") or {}
        live = request.query_params.get("live_preview") == "true"
        if live:
            # The browser draws text and media layers. Opening this pane must
            # never wait for hook/caption FFmpeg encodes of the entire clip.
            preview = await runtime["_deliver"](request, job_id, base.name)
            caption_free = preview
        else:
            preview = None
            if record.get("mode") == "manual" and record.get("overlay_fingerprint") == overlay_fingerprint(clip, base):
                candidate = record.get("base_preview_file")
                if isinstance(candidate, str) and Path(candidate).name == candidate and (root / candidate).is_file():
                    preview = candidate
            elif not record.get("cues") and not record.get("source_cues") and not record.get("post_effects"):
                preview = served
            if preview is None:
                try:
                    preview = await asyncio.to_thread(base_preview, root, clip_index, clip, data, base, had_captions, hook_layer)
                except Exception:
                    raise HTTPException(409, "The image-free preview could not be prepared. Your video is unchanged.") from None
            preview = await runtime["_deliver"](request, job_id, preview)
            caption_free = await asyncio.to_thread(base_preview, root, clip_index, clip, data, base, False, hook_layer) if had_captions else preview
            caption_free = await runtime["_deliver"](request, job_id, caption_free)
        return {"job_id": job_id, "clip": dict(clip),
                "preview_hook": clip.get("auto_hook") if live and hook_layer else None,
                "captions_above_media": record.get("captions_above_media", True),
                "captions": preview_captions(clip, data) if had_captions else [],
                "caption_style": clip.get("caption_style"),
                "caption_free_video_url": runtime["_signed_media_url"](f"/videos/{job_id}/{caption_free}"),
                "placements": public_placements(job_id, record), "duration": duration,
                "post_effects": record.get("post_effects") or [],
                "width": info["width"], "height": info["height"], "revision": revision(clip),
                "video_url": runtime["_signed_media_url"](f"/videos/{job_id}/{served}"),
                "base_video_url": runtime["_signed_media_url"](f"/videos/{job_id}/{preview}"),
                "max_image_bytes": manual.MAX_IMAGE_BYTES, "max_video_bytes": manual.MAX_VIDEO_BYTES,
                "max_audio_bytes": manual.MAX_AUDIO_BYTES, "max_placements": manual.MAX_PLACEMENTS}

    @router.post("/assets")
    async def upload_image(job_id: str, clip_index: int, request: Request, file: UploadFile = File(...)):
        job_id, _, root, _, _, _, _ = await load(request, job_id, clip_index, editing=True)
        content = await file.read(max(manual.MAX_IMAGE_BYTES, manual.MAX_VIDEO_BYTES, manual.MAX_AUDIO_BYTES) + 1)
        try:
            saved = await asyncio.to_thread(manual.save_media, root, clip_index, content, file.filename or "", file.content_type or "")
        except manual.ManualBrollError as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            await file.close()
        return {**saved, "media_url": image_url(job_id, saved["asset"]),
                "image_url": image_url(job_id, saved["asset"])}

    @router.put("")
    async def put_broll(job_id: str, clip_index: int, request: Request):
        # Reuse the same lock as clip cuts/reframes/AI editing, including
        # multiple clips in a job sharing one metadata file.
        job_id = await resolve_job_id(job_id)
        lock = runtime["_rerender_locks"].setdefault(job_id, asyncio.Lock())
        async with lock:
            job_id, job, root, metadata, data, clip, served = await load(request, job_id, clip_index, editing=True)
            try:
                body = await request.json()
            except (ValueError, TypeError):
                raise HTTPException(400, "Send media placements as JSON.") from None
            if not isinstance(body, dict) or "placements" not in body:
                raise HTTPException(400, "Send a media placements list.")
            previous = clip.get("broll") or {}
            post_effects = list(previous.get("post_effects") or [])
            captions_above = body.get("captions_above_media", (clip.get("broll") or {}).get("captions_above_media", True))
            if type(captions_above) is not bool:
                raise HTTPException(400, "Choose whether captions appear above added media.")
            if body.get("revision") is not None and body["revision"] != revision(clip):
                raise HTTPException(409, "This clip changed while the editor was open. Reopen the editor to load the latest version.")
            import broll
            import layout_ranges
            base, had_captions, hook_layer = await asyncio.to_thread(resolve_base, root, clip, served)
            info = await asyncio.to_thread(broll._probe_video, str(base))
            try:
                placements = await asyncio.to_thread(manual.validate_placements, body["placements"], info["duration"], root)
            except manual.ManualBrollError as exc:
                raise HTTPException(400, str(exc)) from None
            segments, _ = runtime["_clip_recipe_parts"](clip)
            fingerprint = overlay_fingerprint(clip, base)
            preview_file = None
            if previous.get("mode") == "manual" and previous.get("overlay_fingerprint") == fingerprint:
                candidate = previous.get("base_preview_file")
                if isinstance(candidate, str) and Path(candidate).name == candidate and (root / candidate).is_file():
                    preview_file = candidate
            elif not previous.get("cues") and not previous.get("source_cues") and not previous.get("post_effects"):
                preview_file = served
            output = root / f"broll_{time.time_ns()}_{base.name}"

            def render():
                nonlocal preview_file
                if preview_file is None:
                    preview_file = base_preview(root, clip_index, clip, data, base, had_captions, hook_layer)
                if placements:
                    broll.render_cutaways(str(base), str(output), placements, info)
                else:
                    shutil.copyfile(base, output)
                    copy_layout(base, output)
                if post_effects:
                    from ai_clip_edit import replay_post_effects
                    replay_post_effects(output, post_effects, info)
                if had_captions and not captions_above:
                    # Keep an uncaptioned counterpart so later caption edits
                    # can still strip this layer without baking text twice.
                    caption_base = restore_overlays(root, clip, data, base, True, None)
                    layered = root / f"subtitled_{time.time_ns()}_{output.name}"
                    if placements:
                        broll.render_cutaways(str(caption_base), str(layered), placements, info)
                    else:
                        shutil.copyfile(caption_base, layered)
                        copy_layout(caption_base, layered)
                    if post_effects:
                        replay_post_effects(layered, post_effects, info)
                    picture = restore_overlays(root, clip, data, layered, False, hook_layer)
                else:
                    picture = restore_overlays(root, clip, data, output, had_captions, hook_layer)
                return picture
            try:
                final = await asyncio.to_thread(render)
                delivered = await runtime["_deliver"](request, job_id, final.name)
                preview_file = await runtime["_deliver"](request, job_id, preview_file)
            except HTTPException:
                raise
            except Exception as exc:
                output.unlink(missing_ok=True)
                print(f"Manual B-roll render failed ({type(exc).__name__}); previous clip retained.")
                raise HTTPException(500, "Media could not be saved. Your previous video is unchanged.") from None
            record = {"mode": "manual", "enabled": bool(placements),
                "captions_above_media": captions_above,
                "status": "applied" if placements else "removed", "media_provider": "upload",
                "file": output.name, "base_file": base.name, "base_preview_file": preview_file,
                "overlay_fingerprint": fingerprint, "failed_cues": 0,
                "cues": [{key: value for key, value in item.items() if key != "path"} for item in placements],
                "source_cues": manual.source_cues(placements, segments)}
            if post_effects:
                record["post_effects"] = post_effects
            url = f"/videos/{job_id}/{delivered}"
            updates = {"broll": record, "video_url": url, "layout_ranges": layout_ranges.read(str(output))}
            clip.update(updates)
            await asyncio.to_thread(runtime["write_json_atomic"], str(metadata), data, indent=2)
            memory = (job.get("result") or {}).get("clips") or []
            mem_clip = find_clip_by_index(memory, clip_index)
            if mem_clip is not None and not mem_clip.get('deleted'):
                mem_clip.update(updates)
            runtime["_archive_clip_edit_bg"](job_id, clip_index, delivered)
            return {"success": True, "video_url": url, "new_video_url": url,
                    "base_video_url": runtime["_signed_media_url"](f"/videos/{job_id}/{preview_file}"),
                    "clip": dict(clip), "placements": public_placements(job_id, record),
                    "revision": revision(clip)}

    app.include_router(router)
