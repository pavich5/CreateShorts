"""User-uploaded images, videos and sounds at explicit clip-relative times."""
from __future__ import annotations

import hashlib
import io
import math
import json
from pathlib import Path
import re
import subprocess
import unicodedata
import uuid

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_AUDIO_BYTES = 30 * 1024 * 1024
MAX_PLACEMENTS = 50
ASSET_RE = re.compile(r"broll_assets_[0-9a-f]{12}/[0-9a-f]{20}\.(?:png|jpg|jpeg|webp|mp4|m4a)")
TRANSFORM_DEFAULTS = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0,
                      "fit": "contain", "opacity": 1.0, "background": "blur"}


class ManualBrollError(ValueError):
    pass


def display_name(value):
    """Keep a bounded user-facing label; it never controls an asset path."""
    if not isinstance(value, str):
        return None
    clean = "".join(char for char in value[:4096] if unicodedata.category(char) != "Cc").strip()[:255]
    return clean or None


def image_transform(entry):
    """A bounded image box in percentages of the video frame."""
    result = {}
    for key in ("x", "y", "width", "height", "opacity"):
        value = entry.get(key, TRANSFORM_DEFAULTS[key])
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ManualBrollError("Image size, position, and opacity must be valid numbers.")
        minimum = .1 if key in ("width", "height") else 0
        maximum = 1 if key == "opacity" else 100
        if not minimum <= value <= maximum:
            raise ManualBrollError("Keep image sizes between 0.1% and 100%, positions inside the frame, and opacity between 0 and 1.")
        result[key] = round(float(value), 6)
    if result["x"] + result["width"] > 100 + 1e-6 or result["y"] + result["height"] > 100 + 1e-6:
        raise ManualBrollError("The image must fit inside the video frame. Reduce its size or change its position.")
    for key, choices in (("fit", {"contain", "cover", "stretch"}), ("background", {"blur", "none"})):
        value = entry.get(key, TRANSFORM_DEFAULTS[key])
        if not isinstance(value, str) or value not in choices:
            raise ManualBrollError("Choose a valid image fit and background.")
        result[key] = value
    return result


def probe_media(path):
    """Inspect only local media containers, never playlists or remote inputs."""
    result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", "mov,matroska,webm,mp3,wav,flac,ogg,aac",
        "-show_entries", "stream=codec_type,width,height,duration:stream_disposition=attached_pic:format=duration",
        "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True)
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float((data.get("format") or {}).get("duration") or max(
        (float(s.get("duration") or 0) for s in streams), default=0))
    if not math.isfinite(duration) or duration <= 0 or not (video or audio):
        raise ValueError("Media has no playable duration")
    return {"kind": "video" if video else "audio", "media_type": "video" if video else "audio",
            "duration": duration, "media_duration": duration, "has_audio": audio,
            "width": int(video.get("width", 0)) if video else 0,
            "height": int(video.get("height", 0)) if video else 0}


def asset_path(root, asset):
    root = Path(root).resolve()
    if not isinstance(asset, str) or not ASSET_RE.fullmatch(asset):
        raise ManualBrollError("Choose media uploaded to this video.")
    path = root / asset
    if path.is_symlink() or path.parent.is_symlink():
        raise ManualBrollError("Media is unavailable.")
    try:
        path.resolve(strict=True).relative_to(root)
        if not path.is_file():
            raise ValueError()
        if path.suffix in {".mp4", ".m4a"}:
            info = probe_media(path)
            if info["kind"] != ("video" if path.suffix == ".mp4" else "audio"):
                raise ValueError()
        else:
            from PIL import Image
            with Image.open(path) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError()
                image.verify()
    except (OSError, ValueError, RuntimeError, ImportError, KeyError, IndexError, subprocess.SubprocessError):
        raise ManualBrollError("Media is unavailable; upload it again.") from None
    return path


def save_image(root, clip_index, content):
    """Decode actual image bytes, remove metadata, and normalize into PNG."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ManualBrollError("Choose an image smaller than 20 MB.")
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"}:
                raise ValueError()
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ManualBrollError("Image is too large; use fewer than 40 megapixels.")
            image.seek(0)
            normalized = ImageOps.exif_transpose(image).convert("RGBA")
            normalized.thumbnail((4096, 4096))
            normalized.info.clear()
            encoded = io.BytesIO()
            normalized.save(encoded, "PNG", optimize=True)
            payload = encoded.getvalue()
    except ManualBrollError:
        raise
    except (OSError, ValueError, ImportError, Image.DecompressionBombError):
        raise ManualBrollError("Upload a valid JPG, PNG, WebP, GIF, BMP, or TIFF image.") from None
    root = Path(root).resolve()
    directory = root / ("broll_assets_" + hashlib.sha256(f"manual:{clip_index}".encode()).hexdigest()[:12])
    if directory.is_symlink():
        raise ManualBrollError("Image folder is unavailable.")
    directory.mkdir(exist_ok=True)
    name = hashlib.sha256(payload).hexdigest()[:20] + ".png"
    path = directory / name
    temporary = directory / ("upload_" + uuid.uuid4().hex + ".png")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path.relative_to(root).as_posix()


def save_media(root, clip_index, content, filename="", content_type=""):
    """Normalize actual uploaded bytes to browser-playable PNG, MP4 or M4A."""
    suffix = Path(filename).suffix.lower()
    is_audio = suffix in {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus"}
    is_video = suffix in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    if not is_audio and not is_video and not content_type.startswith(("audio/", "video/")):
        asset = save_image(root, clip_index, content)
        from PIL import Image
        with Image.open(Path(root) / asset) as image:
            return {"asset": asset, "kind": "image", "media_type": "image",
                    "width": image.width, "height": image.height, "has_audio": False}
    limit = MAX_AUDIO_BYTES if is_audio or content_type.startswith("audio/") else MAX_VIDEO_BYTES
    if not content or len(content) > limit:
        raise ManualBrollError(f"Choose {'audio smaller than 30' if limit == MAX_AUDIO_BYTES else 'a video smaller than 100'} MB.")
    root = Path(root).resolve()
    directory = root / ("broll_assets_" + hashlib.sha256(f"manual:{clip_index}".encode()).hexdigest()[:12])
    if directory.is_symlink():
        raise ManualBrollError("Media folder is unavailable.")
    directory.mkdir(exist_ok=True)
    token = uuid.uuid4().hex
    upload = directory / f"upload_{token}.bin"
    normalized = directory / f"normalized_{token}.mp4"
    try:
        upload.write_bytes(content)
        info = probe_media(upload)
        kind = info["kind"]
        if (is_audio or content_type.startswith("audio/")) and kind != "audio":
            raise ManualBrollError("Choose an audio file for a sound effect.")
        if kind == "audio" and len(content) > MAX_AUDIO_BYTES:
            raise ManualBrollError("Choose audio smaller than 30 MB.")
        if info["duration"] > 600:
            raise ManualBrollError("Choose media up to 10 minutes long.")
        normalized = normalized.with_suffix(".mp4" if kind == "video" else ".m4a")
        command = ["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", "mov,matroska,webm,mp3,wav,flac,ogg,aac", "-i", str(upload)]
        if kind == "video":
            from ffmpeg_utils import QUALITY_FAST, video_encode_args
            command += ["-map", "0:v:0", "-map", "0:a:0?", "-vf",
                "scale=w='min(1920,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
                *video_encode_args(QUALITY_FAST), "-pix_fmt", "yuv420p"]
        else:
            command += ["-map", "0:a:0", "-vn"]
        command += ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-map_metadata", "-1",
                    "-map_chapters", "-1", "-movflags", "+faststart", str(normalized)]
        subprocess.run(command, capture_output=True, text=True, timeout=300, check=True)
        info = probe_media(normalized)
        name = hashlib.sha256(normalized.read_bytes()).hexdigest()[:20] + normalized.suffix
        path = directory / name
        normalized.replace(path)
        return {"asset": path.relative_to(root).as_posix(), **info}
    except ManualBrollError:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, subprocess.SubprocessError):
        raise ManualBrollError("Upload a valid video (MP4, MOV, WebM) or audio file (MP3, WAV, M4A, OGG, AAC, FLAC).") from None
    finally:
        upload.unlink(missing_ok=True)
        normalized.unlink(missing_ok=True)


def media_options(entry, kind):
    animation = entry.get("animation", "none")
    if animation not in {"none", "fade", "slide_up", "zoom_in"}:
        raise ManualBrollError("Choose a supported entrance animation.")
    result = {"kind": kind, "media_type": kind, "animation": animation}
    for key, default, low, high in (("animation_duration", .3, .05, 5),
                                   ("volume", 1 if kind == "audio" else 0, 0, 2),
                                   ("animation_elapsed", 0, 0, 600)):
        value = entry.get(key, default)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ManualBrollError("Use animation lengths from 0.05 to 5 seconds and sound volume from 0 to 2.")
        result[key] = round(float(value), 6)
    if kind == "video":
        result["volume"] = 0.0
    return result


def validate_placements(raw, duration, root):
    if not isinstance(raw, list) or len(raw) > MAX_PLACEMENTS:
        raise ManualBrollError("Add up to 50 media placements per clip.")
    valid, ids = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ManualBrollError("Invalid image placement.")
        start, end = entry.get("start"), entry.get("end")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end - start < .05 - 1e-6 or end > duration + .025):
            raise ManualBrollError("Image times must be inside the video, with the end after the start.")
        asset = entry.get("asset")
        path = asset_path(root, asset)
        kind = "video" if path.suffix == ".mp4" else "audio" if path.suffix == ".m4a" else "image"
        if entry.get("kind", entry.get("media_type", kind)) != kind:
            raise ManualBrollError("The placement type does not match its uploaded media.")
        options = media_options(entry, kind)
        name = display_name(entry.get("name"))
        if name:
            options["name"] = name
        if kind != "image":
            info = probe_media(path)
            media_start = entry.get("source_start", entry.get("media_start", 0))
            if (type(media_start) not in (int, float) or not math.isfinite(media_start)
                    or media_start < 0 or media_start + min(end, duration) - start > info["duration"] + .025):
                raise ManualBrollError("Choose a source start and placement length inside the uploaded media.")
            options.update(media_start=round(float(media_start), 3), media_duration=info["duration"],
                           has_audio=info["has_audio"], media_width=info["width"], media_height=info["height"])
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
            identifier = uuid.uuid4().hex
        if identifier in ids:
            raise ManualBrollError("Each image placement needs its own identifier.")
        ids.add(identifier)
        if "layer" in entry:
            layer = entry["layer"]
            if type(layer) is not int or not 0 <= layer <= 9999:
                raise ManualBrollError("Choose a valid media layer order.")
            options["layer"] = layer
        valid.append({"id": identifier, "placement_id": identifier, "asset": asset,
                      "start": round(float(start), 3), "end": round(min(float(end), duration), 3),
                      "duration": round(min(float(end), duration) - float(start), 3),
                      "mode": "manual", "motion": False, "path": str(path), **image_transform(entry), **options})
    valid.sort(key=lambda item: (item["start"], item["end"]))
    return valid


def source_cues(placements, segments):
    """Map chosen display intervals onto the clip's ordered original footage."""
    result, offset = [], 0.0
    for segment_index, segment in enumerate(segments):
        source_start, source_end = float(segment["start"]), float(segment["end"])
        for cue in placements:
            start, end = max(cue["start"], offset), min(cue["end"], offset + source_end - source_start)
            if end <= start:
                continue
            first, last = source_start + start - offset, source_start + end - offset
            # Source footage can intentionally occur more than once in a cut.
            # Preserve which occurrence the user placed this image over.
            boundaries = {first, last}
            for previous in segments[:segment_index]:
                for edge in (float(previous["start"]), float(previous["end"])):
                    if first < edge < last:
                        boundaries.add(edge)
            points = sorted(boundaries)
            for left, right in zip(points, points[1:]):
                midpoint = (left + right) / 2
                occurrence = sum(float(previous["start"]) <= midpoint < float(previous["end"])
                                 for previous in segments[:segment_index])
                display_start, display_end = offset + left - source_start, offset + right - source_start
                result.append({key: value for key, value in cue.items() if key != "path"}
                              | {"source_start": round(left, 3), "source_end": round(right, 3),
                                 "source_occurrence": occurrence,
                                 "start": round(display_start, 3), "end": round(display_end, 3),
                                 "duration": round(display_end - display_start, 3)}
                              | ({"media_start": round(cue.get("media_start", 0) + display_start - cue["start"], 3)}
                                 if cue.get("media_type") in {"video", "audio"} else {})
                              | ({"animation_elapsed": round(cue.get("animation_elapsed", 0) + display_start - cue["start"], 3)}
                                 if cue.get("animation", "none") != "none" else {}))
        offset += source_end - source_start
    return result


def displayed_placements(record):
    """Merge adjacent fragments of a placement split by a source edit seam."""
    if not isinstance(record, dict) or record.get("mode") != "manual":
        return []
    result = []
    for index, cue in enumerate(sorted(record.get("cues") or [], key=lambda item: item.get("start", 0))):
        if not isinstance(cue, dict) or not isinstance(cue.get("asset"), str):
            continue
        identifier = cue.get("placement_id") or cue.get("id") or f"saved_{index}"
        transform = image_transform(cue)
        options = {key: cue[key] for key in ("kind", "media_type", "media_start", "media_duration", "has_audio",
            "media_width", "media_height", "volume", "animation", "animation_duration", "animation_elapsed", "layer") if key in cue}
        name = display_name(cue.get("name"))
        if name:
            options["name"] = name
        # Keep old stationary-image records compatible with their original
        # public shape. The media editor defaults absent kind to image.
        if cue.get("media_type", "image") == "image" and cue.get("animation", "none") == "none":
            for key in ("kind", "media_type", "volume", "animation", "animation_duration", "animation_elapsed"):
                options.pop(key, None)
        previous = next((item for item in reversed(result) if item["id"] == identifier), None)
        continuous_media = (cue.get("media_type") not in {"video", "audio"} or previous is not None and
            abs(previous.get("media_start", 0) + previous["end"] - previous["start"] - cue.get("media_start", 0)) < .002)
        if (previous and previous["asset"] == cue["asset"] and continuous_media
                and abs(previous["end"] - cue["start"]) < .002
                and all(previous[key] == value for key, value in transform.items())
                and all(previous.get(key) == value for key, value in options.items()
                        if key not in {"media_start", "animation_elapsed"})):
            previous["end"] = cue["end"]
        else:
            result.append({"id": identifier, "asset": cue["asset"],
                           "start": cue["start"], "end": cue["end"], **transform, **options})
    # Repeated source segments can produce the same placement identifier at
    # separated display positions. They are independently editable in the UI.
    seen = set()
    for item in result:
        if item["id"] in seen:
            item["id"] = uuid.uuid4().hex
        seen.add(item["id"])
    return result
