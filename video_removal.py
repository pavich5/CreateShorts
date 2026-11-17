"""Permanent, per-video removal with stable clip indices and durable tombstones."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.dependencies.utils import get_dependant
from fastapi.routing import APIRoute

from jsonfile import write_json_atomic

MANIFEST = ".deleted_clips.json"
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,150}\Z")
_FILE_ID = re.compile(r"[0-9a-f]{64}\Z")
_REFERENCE_FIELDS = {"video_url", "file", "asset", "base_file", "base_preview_file",
                     "server_file", "original_file", "input_filename", "source"}


def _read(path, default=None):
    try:
        if path.is_symlink():
            return default
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _relative(value, job_id):
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc:
        return None
    name = unquote(parsed.path)
    if name.startswith("/videos/" + job_id + "/"):
        name = name[len("/videos/" + job_id + "/"):]
    elif name.startswith("/"):
        return None
    parts = PurePosixPath(name).parts
    if not parts or any(p in ("..", ".") for p in parts):
        return None
    return "/".join(parts)


def _references(value, job_id):
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _REFERENCE_FIELDS:
                relative = _relative(child, job_id)
                if relative:
                    found.add(relative)
            found.update(_references(child, job_id))
    elif isinstance(value, list):
        for child in value:
            found.update(_references(child, job_id))
    return found


def _safe_path(root, relative):
    """Validate lexically and reject symlinks in every component, even missing files."""
    relative = _relative(relative, "")
    if not relative:
        raise HTTPException(400, "Invalid video asset path")
    root = Path(root)
    if root.is_symlink():
        raise HTTPException(400, "Invalid video directory")
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise HTTPException(409, "A video asset is a symbolic link; removal was stopped.")
    if not current.resolve().is_relative_to(root.resolve()):
        raise HTTPException(400, "Invalid video asset path")
    return current


def read_deleted_indices(root):
    manifest = _read(Path(root) / MANIFEST, {}) or {}
    return {int(i) for i in manifest.get("clips", {}) if str(i).isdigit()}


def media_deleted(root, relative):
    manifest = _read(Path(root) / MANIFEST, {}) or {}
    return relative in manifest.get("blocked_files", [])


def apply_tombstones(root, metadata, deleted_indices=()):
    """Merge remote/local deletion markers before recovery can manufacture URLs."""
    deleted = read_deleted_indices(root) | set(deleted_indices)
    clips = metadata.get("shorts") or []
    for i, clip in enumerate(clips):
        if i in deleted or clip.get("deleted"):
            clips[i] = {"deleted": True, "index": i, "video_url": None}
        else:
            clip["index"] = i
    return metadata


def plan_clip_files(root, job_id, index, metadata, extra_references=(), extra_names=()):
    """Attribute all versions to one clip, protecting sibling/source dependencies."""
    root = Path(root)
    clips = metadata.get("shorts") or []
    selected = clips[index]
    references = _references(selected, job_id) | set(extra_references)
    protected = set()
    for i, clip in enumerate(clips):
        if i != index and not clip.get("deleted"):
            protected.update(_references(clip, job_id))
    live_siblings = any(i != index and not c.get("deleted") for i, c in enumerate(clips))
    source = _relative(metadata.get("source_video"), job_id)
    if source and live_siblings:
        protected.add(source)
    names = {str(p.relative_to(root).as_posix()) for p in root.rglob("*")
             if not p.is_dir() or p.is_symlink()} | set(extra_names)
    pattern = re.compile(r"(?:^|_)clip_" + str(index + 1) + r"(?:[._]|$)")
    for i, clip in enumerate(clips):
        if i != index and not clip.get("deleted"):
            sibling_pattern = re.compile(r"(?:^|_)clip_" + str(i + 1) + r"(?:[._]|$)")
            protected.update(name for name in names if sibling_pattern.search(PurePosixPath(name).name))
    references.update(name for name in names if pattern.search(PurePosixPath(name).name))
    # Uploads can exist before the user saves their image placements.
    cache_ids = {hashlib.sha256(f"manual:{index}".encode()).hexdigest()[:12]}
    for meta in root.glob("*_metadata.json"):
        base = meta.name.removesuffix("_metadata.json") + f"_clip_{index + 1}.mp4"
        cache_ids.add(hashlib.sha256(base.encode()).hexdigest()[:12])
    references.update(name for name in names if any(
        part in {"broll_assets_" + cache_id for cache_id in cache_ids}
        for part in PurePosixPath(name).parts))
    # A hook recipe records its exact input even when an AI edit uses a UUID name.
    recipes = {}
    for path in root.glob(".hookrecipe_*.json"):
        recipe = _read(path, {}) or {}
        output = path.name[len(".hookrecipe_"):-len(".json")]
        recipes[output] = (_references(recipe, job_id), path.name)
    def closure(seed):
        found = set(seed)
        changed = True
        while changed:
            old = set(found)
            for output, (dependencies, recipe_name) in recipes.items():
                if output in found:
                    found.update({recipe_name, *dependencies})
                    image = ".hookrecipe_" + output + ".png"
                    if image in names:
                        found.add(image)
            for name in list(found):
                layer = re.match(r"^(?:wm_|(?:subtitled|hooked|recut|broll)_\d+_|hook_)(.+)$", name)
                if layer:
                    found.add(layer[1])
            for name in names:
                if any(name == ref + ".layout.json" or name == ref + ".srt"
                       or name == ref + ".ass" for ref in found):
                    found.add(name)
            changed = found != old
        return found
    protected = closure(protected)
    references = closure(references)
    if source and not live_siblings:
        references.add(source)
    # Shared assets, even if referenced by a selected hook, remain for siblings.
    references.difference_update(protected)
    for name in references:
        _safe_path(root, name)
    return sorted(references)


def _metadata(root):
    paths = sorted(Path(root).glob("*_metadata.json"))
    if not paths or paths[0].is_symlink():
        raise HTTPException(404, "Project metadata not found")
    data = _read(paths[0])
    if not isinstance(data, dict) or not isinstance(data.get("shorts"), list):
        raise HTTPException(409, "Project metadata could not be read; no files were removed.")
    return paths[0], data


def _library_plan(runtime, requested_job, index):
    """Resolve all saved aliases of a logical video, including older Saved copies."""
    if runtime["BILLING_ENABLED"]:
        return requested_job, index, [], [], None
    from folder_library import file_id
    library = runtime["LOCAL_FOLDER_LIBRARY"]
    store = runtime["LOCAL_CAMPAIGNS"]
    with store.lock:
        data = store.snapshot()
        origin = f"{requested_job}/clip/{index}"
        selected_relative = None
        if requested_job.startswith("file:"):
            fid = requested_job[5:]
            if index != 0 or not _FILE_ID.fullmatch(fid):
                raise HTTPException(404, "Video not found")
            ledger = (data.get("video_removals") or {}).get(fid)
            if ledger:
                return ledger["job_id"], ledger["clip_index"], ledger["relatives"], ledger["file_ids"], fid
            try:
                _, row = library.locate(fid)
                selected_relative = row["relative_path"]
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            origin = data.get("editable_sources", {}).get(selected_relative) or "file/" + fid
        relatives = {rel for rel, key in data.get("editable_sources", {}).items() if key == origin}
        saved = data.get("saved_sources", {}).get(origin)
        if saved:
            relatives.add(saved)
        if selected_relative:
            relatives.add(selected_relative)
        if origin.startswith("file/"):
            original_id = origin[5:]
            for row in library.scan():
                if row["local_file_id"] == original_id:
                    relatives.add(row["relative_path"])
            job_id, clip_index = requested_job, index
        else:
            match = re.fullmatch(r"([A-Za-z0-9_-]{1,150})/clip/(\d+)", origin)
            job_id, clip_index = (match[1], int(match[2])) if match else (requested_job, index)
        relatives = sorted(relatives)
        for relative in relatives:
            _safe_path(library.root, relative)
        ids = sorted({file_id(relative) for relative in relatives})
        fid = requested_job[5:] if requested_job.startswith("file:") else None
        return job_id, clip_index, relatives, ids, fid


async def _cloud_context(runtime, request, job_id, index):
    if not runtime["BILLING_ENABLED"]:
        return None, set(), []
    from cloud.auth import get_session_user_required
    from cloud import database, storage
    from cloud.models import Project, UserVideo
    from sqlalchemy import select
    user = await get_session_user_required(request)
    async with database.session() as session:
        project = (await session.execute(select(Project).where(
            Project.job_id == job_id, Project.user_id == user.id))).scalar_one_or_none()
        if project is None:
            raise HTTPException(404, "Project not found")
        state = project.state or {}
        entry = next((c for c in state.get("clips", []) if c.get("index") == index), {})
        references = _references(entry, job_id)
        references.update((state.get("deleted_files") or {}).get(str(index), []))
        videos = (await session.execute(select(UserVideo).where(
            UserVideo.user_id == user.id, UserVideo.job_id == job_id,
            UserVideo.clip_index == index))).scalars().all()
        prefix = storage.job_key(user.id, job_id, "")
        references.update(v.r2_key[len(prefix):] for v in videos if v.r2_key.startswith(prefix))
    keys = await asyncio.to_thread(storage.list_keys, prefix)
    return user.id, references, [key[len(prefix):] for key in keys if key.startswith(prefix)]


async def _cloud_remove(user_id, job_id, index, files, metadata_path, root):
    """Commit the durable deletion guard first, then purge its media objects."""
    from cloud import database, storage
    from cloud.models import Project, UserVideo
    from sqlalchemy import select, delete
    async with database.session() as session:
        async with session.begin():
            project = (await session.execute(select(Project).where(
                Project.job_id == job_id, Project.user_id == user_id).with_for_update())).scalar_one_or_none()
            if project is None:
                raise HTTPException(404, "Project not found")
            state = dict(project.state or {})
            # A background archive may have finished between the initial plan
            # and this row lock. Re-read its filenames while holding the same
            # lock as archive_clip_edit, so its final object cannot escape.
            latest = _read(metadata_path, {}) or {}
            clips = latest.get("shorts") or []
            entry_refs = set((state.get("deleted_files") or {}).get(str(index), []))
            for entry in state.get("clips", []):
                current_index = entry.get("index")
                if isinstance(current_index, int) and 0 <= current_index < len(clips):
                    clips[current_index] = {**clips[current_index], **entry}
                if current_index == index:
                    entry_refs.update(_references(entry, job_id))
            prefix = storage.job_key(user_id, job_id, "")
            videos = (await session.execute(select(UserVideo).where(UserVideo.user_id == user_id,
                UserVideo.job_id == job_id, UserVideo.clip_index == index))).scalars().all()
            entry_refs.update(v.r2_key[len(prefix):] for v in videos if v.r2_key.startswith(prefix))
            keys = await asyncio.to_thread(storage.list_keys, prefix)
            names = [key[len(prefix):] for key in keys if key.startswith(prefix)]
            files[:] = sorted(set(files) | set(plan_clip_files(root, job_id, index, latest, entry_refs, names)))
            manifest = _read(root / MANIFEST, {}) or {}
            manifest["clips"][str(index)]["files"] = files
            manifest["blocked_files"] = sorted(set(manifest.get("blocked_files", [])) | set(files))
            write_json_atomic(str(root / MANIFEST), manifest, indent=2)
            state["clips"] = [c for c in state.get("clips", []) if c.get("index") != index]
            state["deleted_indices"] = sorted(set(state.get("deleted_indices", [])) | {index})
            deleted_files = dict(state.get("deleted_files") or {})
            deleted_files[str(index)] = sorted(set(deleted_files.get(str(index), [])) | set(files))
            state["deleted_files"] = deleted_files
            project.state = state
            await session.execute(delete(UserVideo).where(UserVideo.user_id == user_id,
                UserVideo.job_id == job_id, UserVideo.clip_index == index))
    errors = []
    for path in (metadata_path, root / MANIFEST):
        try:
            await asyncio.to_thread(storage.upload_file, str(path),
                storage.job_key(user_id, job_id, path.name), "application/json")
        except Exception:
            errors.append("Cloud deletion metadata could not be saved")
    for name in files:
        try:
            await asyncio.to_thread(storage.delete_key, storage.job_key(user_id, job_id, name))
        except Exception:
            errors.append("A cloud video asset could not be deleted")
    return errors


def install_video_removal_routes(app, runtime):
    router = APIRouter()
    active = {}
    removing = set()

    async def video_activity(request: Request):
        """Protect old render endpoints too: no delete can race an active request."""
        target = {**dict(request.query_params), **dict(request.path_params)}
        if request.method in {"POST", "PUT", "PATCH"}:
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    body = await request.json()
                    if isinstance(body, dict):
                        target.update(body)
                except ValueError:
                    pass
            elif "form" in content_type and request.url.path == "/api/edit":
                target.update(await request.form())
        # Route identity wins over extra body/query fields. Otherwise an
        # ignored JSON job_id could bypass the actual path's generation guard
        # and acquire the activity lease for a different project.
        target.update(request.path_params)
        job_id = target.get("job_id")
        if not job_id and isinstance(target.get("source_key"), str):
            source = target["source_key"]
            job_id = "file:" + source[5:] if source.startswith("file/") else source.split("/clip/", 1)[0]
            if "/clip/" in source:
                target.setdefault("clip_index", source.rsplit("/clip/", 1)[1])
        if not job_id and target.get("file_id"):
            job_id = "file:" + str(target["file_id"])
        if not isinstance(job_id, str):
            yield
            return
        job = runtime['jobs'].get(job_id)
        if (request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and job
                and (job.get('status') in {'queued', 'processing'} or job.get('delivering'))):
            # The generator still owns and will rewrite the complete metadata
            # document. Permit previews, but never accept edits it would erase.
            await runtime['_assert_job_owner'](request, job)
            raise HTTPException(409, 'Wait until clip generation finishes before editing this video.')
        if job_id in removing:
            raise HTTPException(409, "This video is being removed.")
        if not runtime["BILLING_ENABLED"]:
            match = re.fullmatch(r"(?:file:|manual_|ai_)([0-9a-f]{64})", job_id)
            if match and match[1] in (runtime["LOCAL_CAMPAIGNS"].snapshot().get("video_removals") or {}):
                raise HTTPException(404, "Video has been removed")
        try:
            index = int(target.get("clip_index", 0))
        except (ValueError, TypeError):
            index = -1
        if index < 0:
            raise HTTPException(404, "Clip not found")
        active[job_id] = active.get(job_id, 0) + 1
        try:
            if _JOB_ID.fullmatch(job_id):
                root = Path(runtime["OUTPUT_DIR"]) / job_id
                if runtime["BILLING_ENABLED"] and job_id not in runtime["jobs"]:
                    await runtime["_ensure_job_files"](job_id, request)
                clips = ((runtime["jobs"].get(job_id) or {}).get("result") or {}).get("clips") or []
                from clip_identity import find_clip_by_index
                current_clip = find_clip_by_index(clips, index)
                if index in read_deleted_indices(root) or (current_clip is not None and current_clip.get("deleted")):
                    raise HTTPException(404, "Video has been removed")
            yield
        finally:
            active[job_id] -= 1

    # These dependencies run before all legacy clip reads/renders, and hold an
    # activity lease until the response completes. Existing render locks remain
    # untouched; removal refuses a busy video instead of deleting its inputs.
    for route in app.routes:
        if isinstance(route, APIRoute) and (route.path.startswith("/api/clip/") or route.path in {
            "/api/edit", "/api/edit/ai", "/api/edit/trim", "/api/effects/generate", "/api/subtitle", "/api/subtitle/remove",
            "/api/hook", "/api/translate", "/api/local/library/save"}):
            route.dependant.dependencies.insert(0, get_dependant(path=route.path, call=video_activity))

    @router.delete("/api/clip/{job_id}/{clip_index}")
    async def remove_video(job_id: str, clip_index: int, request: Request):
        requested_job, requested_index = job_id, clip_index
        if clip_index < 0 or (not _JOB_ID.fullmatch(job_id) and not (
                not runtime["BILLING_ENABLED"] and job_id.startswith("file:") and _FILE_ID.fullmatch(job_id[5:]))):
            raise HTTPException(404, "Video not found")
        # Editable library drafts represent the same underlying local video.
        draft_alias = re.fullmatch(r"(?:manual_|ai_)([0-9a-f]{64})", job_id)
        if draft_alias and not runtime["BILLING_ENABLED"]:
            job_id = "file:" + draft_alias[1]
        job_id, clip_index, relatives, file_ids, alias_id = _library_plan(runtime, job_id, clip_index)
        draft_jobs = sorted({prefix + fid for fid in file_ids for prefix in ("manual_", "ai_")})
        keys = {job_id, requested_job, *draft_jobs, *("file:" + fid for fid in file_ids)}
        if any(active.get(key, 0) or key in removing for key in keys):
            raise HTTPException(409, "This video is being edited. Wait for the edit to finish, then remove it.")
        removing.update(keys)
        try:
            async with AsyncExitStack() as stack:
                for key in sorted(keys):
                    await stack.enter_async_context(runtime["_rerender_locks"].setdefault(key, asyncio.Lock()))
                return await remove_locked(job_id, clip_index, requested_job, requested_index,
                                           relatives, file_ids, alias_id, draft_jobs, request)
        finally:
            removing.difference_update(keys)

    async def remove_locked(job_id, index, requested_job, requested_index, relatives, file_ids, alias_id, drafts, request):
        output = Path(runtime["OUTPUT_DIR"]).resolve()
        cloud_user, cloud_refs, cloud_names = await _cloud_context(runtime, request, job_id, index)
        generated = not job_id.startswith("file:")
        root = output / job_id if generated else None
        files, metadata_path, data, remaining = [], None, None, 0
        source_uploads = []
        if generated:
            await runtime["_ensure_job_files"](job_id, request)
            job = runtime["jobs"].get(job_id)
            if not job:
                raise HTTPException(404, "Job not found")
            await runtime["_assert_job_owner"](request, job)
            presented = runtime.get("_presented_status", lambda _id, record: record.get("status"))(job_id, job)
            if presented != "completed" or job.get("delivering"):
                raise HTTPException(409, "Wait for this video to finish processing before removing it.")
            if root.is_symlink() or root.resolve().parent != output:
                raise HTTPException(400, "Invalid project directory")
            metadata_path, data = _metadata(root)
            clips = data["shorts"]
            if index >= len(clips):
                raise HTTPException(404, "Clip not found")
            manifest = _read(root / MANIFEST, {}) or {}
            previous = (manifest.get("clips") or {}).get(str(index), {})
            files = sorted(set(plan_clip_files(root, job_id, index, data, cloud_refs, cloud_names)) |
                           set(previous.get("files", [])))
            remaining = sum(i != index and not c.get("deleted") for i, c in enumerate(clips))
            if not remaining and runtime.get("UPLOAD_DIR"):
                uploads = Path(runtime["UPLOAD_DIR"]).resolve()
                for path in uploads.glob(job_id + "_*"):
                    safe = _safe_path(uploads, path.name)
                    if safe.is_file():
                        source_uploads.append(safe)
            for name in files:
                _safe_path(root, name)
        # Validate every linked draft before writing any deletion marker.
        for draft in drafts:
            path = output / draft
            if path.is_symlink():
                raise HTTPException(409, "A linked draft is a symbolic link; removal was stopped.")
            draft_job = runtime["jobs"].get(draft)
            if draft_job and draft_job.get("status") not in {"completed", "failed"}:
                raise HTTPException(409, "A linked draft is still processing.")
            if path.is_dir():
                for child in path.rglob("*"):
                    _safe_path(path, child.relative_to(path).as_posix())
        if not runtime["BILLING_ENABLED"]:
            store = runtime["LOCAL_CAMPAIGNS"]
            with store.lock:
                campaigns = store.snapshot()
                ledger = dict(campaigns.get("video_removals") or {})
                entry = {"job_id": job_id, "clip_index": index, "relatives": relatives, "file_ids": file_ids}
                for fid in file_ids:
                    ledger[fid] = entry
                campaigns["video_removals"] = ledger
                store._write(campaigns)
        if generated:
            manifest.setdefault("version", 1)
            manifest.setdefault("clips", {})[str(index)] = {"files": files, "status": "pending"}
            manifest["blocked_files"] = sorted(set(manifest.get("blocked_files", [])) | set(files))
            write_json_atomic(str(root / MANIFEST), manifest, indent=2)
            data["shorts"][index] = {"deleted": True, "index": index, "video_url": None}
            write_json_atomic(str(metadata_path), data, indent=2)
            memory_clips = (job.get("result") or {}).get("clips") or []
            for position, memory_clip in enumerate(memory_clips):
                if memory_clip.get("index", position) == index:
                    memory_clips[position] = dict(data["shorts"][index])
            (job.get("ready_files") or {}).pop(index, None)
        errors = []
        if cloud_user is not None:
            try:
                errors.extend(await _cloud_remove(cloud_user, job_id, index, files, metadata_path, root))
            except Exception as exc:
                raise HTTPException(503, {"message": "Cloud library removal is incomplete. Retry Remove to finish.",
                                          "retryable": True}) from exc
        for name in files:
            try:
                _safe_path(root, name).unlink(missing_ok=True)
            except (OSError, HTTPException):
                errors.append("A local video asset could not be deleted")
        for path in source_uploads:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                errors.append("The original video upload could not be deleted")
        if not runtime["BILLING_ENABLED"]:
            library = runtime["LOCAL_FOLDER_LIBRARY"]
            for relative in relatives:
                try:
                    _safe_path(library.root, relative).unlink(missing_ok=True)
                except (OSError, HTTPException):
                    errors.append("A saved video could not be deleted")
            for draft in drafts:
                path = output / draft
                try:
                    # Unlink individually after validating above; never follow a
                    # symlink via rmtree, and leave a tombstone for old API URLs.
                    if path.is_dir():
                        for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                            safe = _safe_path(path, child.relative_to(path).as_posix())
                            safe.rmdir() if safe.is_dir() else safe.unlink(missing_ok=True)
                        write_json_atomic(str(path / MANIFEST), {"version": 1, "clips": {"0": {
                            "files": [], "status": "complete"}}, "blocked_files": []}, indent=2)
                    runtime["jobs"].pop(draft, None)
                except (OSError, HTTPException):
                    errors.append("A linked editable draft could not be deleted")
            if not errors:
                with store.lock:
                    campaigns = store.snapshot()
                    identities = {f"{job_id}/clip/{index}", *("file/" + fid for fid in file_ids)}
                    campaigns["deleted_clips"] = sorted(set(campaigns.get("deleted_clips", [])) | identities)
                    if generated and not remaining:
                        identities.add(job_id + "/project")
                    campaigns["clips"] = {key: value for key, value in campaigns.get("clips", {}).items()
                                          if key not in identities}
                    campaigns["editable_sources"] = {relative: origin for relative, origin in
                        campaigns.get("editable_sources", {}).items() if relative not in relatives}
                    campaigns["saved_sources"] = {origin: relative for origin, relative in
                        campaigns.get("saved_sources", {}).items() if relative not in relatives and origin not in identities}
                    campaigns["library_seen"] = [fid for fid in campaigns.get("library_seen", []) if fid not in file_ids]
                    store._write(campaigns)
        if generated:
            manifest = _read(root / MANIFEST, manifest) or manifest
            manifest["clips"][str(index)]["status"] = "pending" if errors else "complete"
            write_json_atomic(str(root / MANIFEST), manifest, indent=2)
        if errors:
            raise HTTPException(503, {"message": "Removal is incomplete. Retry Remove to finish deleting the remaining files.",
                                      "errors": sorted(set(errors)), "retryable": True})
        clip_ids = [{"job_id": job_id, "clip_index": index}] if generated else []
        clip_ids.extend({"job_id": draft, "clip_index": 0} for draft in drafts)
        return {"success": True, "removed": {"job_id": requested_job, "clip_index": requested_index,
            "file_ids": file_ids, "clip_ids": clip_ids, "job_ids": drafts}, "remaining_clips": remaining}

    app.include_router(router)
