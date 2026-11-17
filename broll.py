"""Local image compositing for manually placed B-roll.

Only caller-supplied media and timings are rendered. This module does not
choose placements, search providers, or generate images.
"""
from __future__ import annotations

import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path

def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _probe_video(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate:format=duration", "-of", "json", path],
        capture_output=True, text=True, timeout=30, check=True)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    width, height = int(stream["width"]), int(stream["height"])
    duration = _number(data["format"]["duration"])
    try:
        fps = float(Fraction(stream.get("avg_frame_rate", "30")))
    except (ZeroDivisionError, ValueError):
        fps = 30.0
    if width <= 0 or height <= 0 or duration is None or duration <= 0:
        raise ValueError("Cannot read the rendered clip dimensions or duration")
    return {"width": width, "height": height, "duration": duration,
            "fps": min(60.0, max(1.0, fps))}


def cutaway_layouts(ranges, cues):
    """Full-frame cutaways use bottom captions even over a split-screen shot."""
    import layout_ranges
    out = []
    for source in layout_ranges.normalise(ranges):
        pieces = [source]
        for cue in cues:
            if cue.get("media_type") == "audio":
                continue
            if cue.get("mode") == "manual" and (
                    cue.get("width", 100) < 99.999 or cue.get("height", 100) < 99.999
                    or cue.get("opacity", 1) < .999):
                continue
            updated = []
            for part in pieces:
                start = max(part["start"], cue["start"])
                end = min(part["end"], cue["end"])
                if end <= start:
                    updated.append(part)
                    continue
                if part["start"] < start:
                    updated.append({**part, "end": start})
                updated.append({"start": start, "end": end, "layout": "general"})
                if end < part["end"]:
                    updated.append({**part, "start": end})
            pieces = updated
        out.extend(pieces)
    return out


def _manual_image_layers(index, cue, info):
    """Compose an image inside its user-selected box; keep alpha for overlays."""
    from manual_broll import image_transform
    transform = image_transform(cue)
    width, height = info["width"], info["height"]
    box_width = min(width, max(2, round(width * transform["width"] / 100) // 2 * 2))
    box_height = min(height, max(2, round(height * transform["height"] / 100) // 2 * 2))
    x = min(width - box_width, max(0, round(width * transform["x"] / 100)))
    y = min(height - box_height, max(0, round(height * transform["y"] / 100)))
    label = f"photo{index}"
    fit = transform["fit"]
    if fit == "contain" and transform["background"] == "blur":
        graph = [
            f"[{index}:v]format=rgba,split=2[bg{index}][fg{index}]",
            f"[bg{index}]scale={box_width}:{box_height}:force_original_aspect_ratio=increase,"
            f"crop={box_width}:{box_height},gblur=sigma=24[blur{index}]",
            f"[fg{index}]scale={box_width}:{box_height}:force_original_aspect_ratio=decrease[fit{index}]",
            f"[blur{index}][fit{index}]overlay=(W-w)/2:(H-h)/2:format=auto,setsar=1[{label}]",
        ]
    else:
        scale = (f"scale={box_width}:{box_height}" if fit == "stretch" else
                 f"scale={box_width}:{box_height}:force_original_aspect_ratio=" +
                 ("increase" if fit == "cover" else "decrease"))
        bounds = (f"crop={box_width}:{box_height}" if fit == "cover" else
                  f"pad={box_width}:{box_height}:(ow-iw)/2:(oh-ih)/2:color=black@0")
        graph = [f"[{index}:v]format=rgba,{scale},{bounds},setsar=1[{label}]"]
    animation = cue.get("animation", "none")
    elapsed = max(0.0, _number(cue.get("animation_elapsed")) or 0.0)
    animation_duration = max(.05, min(5.0, _number(cue.get("animation_duration")) or .3))
    filters = (f"format=rgba,colorchannelmixer=aa={transform['opacity']:g},"
               f"format=yuva420p,fps={info['fps']:g},trim=duration={cue['duration']},"
               f"setpts=PTS-STARTPTS+{elapsed}/TB")
    if animation in {"fade", "zoom_in"}:
        filters += f",fade=t=in:st=0:d={animation_duration}:alpha=1"
    if animation == "zoom_in":
        progress = f"min(1,t/{animation_duration})"
        filters += (f",scale=w='max(2,trunc({box_width}*(0.75+0.25*{progress})/2)*2)':"
                    f"h='max(2,trunc({box_height}*(0.75+0.25*{progress})/2)*2)':eval=frame")
        x = f"{x}+({box_width}-w)/2"
        y = f"{y}+({box_height}-h)/2"
    elif animation == "slide_up":
        y = f"{y}+{box_height}*0.25*(1-min(1,max(0,(t-{cue['start']}+{elapsed})/{animation_duration})))"
    graph.append(f"[{label}]{filters},setpts=PTS-STARTPTS+{cue['start']}/TB[cut{index}]")
    return graph, x, y


def _has_audio(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30, check=True)
    return bool(result.stdout.strip())


def render_cutaways(input_path, output_path, cues, info=None):
    """Replace short visual stretches; copy source speech and preserve length."""
    from ffmpeg_utils import METADATA_SCRUB, QUALITY_FAST, video_encode_args
    info = info or _probe_video(input_path)
    cues = [cue for _, cue in sorted(enumerate(cues), key=lambda pair: pair[1].get("layer", pair[0]))]
    width, height = info["width"], info["height"]
    fps, duration = info["fps"], info["duration"]
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", input_path]
    graph = ["[0:v]setpts=PTS-STARTPTS[base]"]
    previous = "base"
    audio_layers = []
    for index, cue in enumerate(cues, 1):
        is_video = cue.get("media_type") == "video"
        is_audio = cue.get("media_type") == "audio"
        manual = cue.get("mode") == "manual"
        if is_video or is_audio:
            media_start = max(0.0, _number(cue.get("media_start")) or 0.0)
            media_duration = _number(cue.get("media_duration"))
            if media_duration is not None and media_duration > 0 and not manual:
                media_start %= media_duration
            command.extend(([] if manual else ["-stream_loop", "-1"]) + ["-ss", str(media_start),
                            "-t", str(cue["duration"]), "-i", cue["path"]])
        else:
            command.extend(["-loop", "1", "-framerate", f"{fps:g}", "-t",
                            str(cue["duration"]), "-i", cue["path"]])
        volume = _number(cue.get("volume"))
        volume = (1.0 if is_audio else 0.0) if volume is None else max(0, min(2, volume))
        if (is_audio or manual and is_video) and volume > 0 and _has_audio(cue["path"]):
            delay = round(cue["start"] * 1000)
            graph.append(f"[{index}:a]atrim=duration={cue['duration']},asetpts=PTS-STARTPTS,"
                         f"aresample=48000,volume={volume:g},adelay={delay}:all=1[sound{index}]")
            audio_layers.append(f"[sound{index}]")
        if is_audio:
            continue
        frames = max(1, round(cue["duration"] * fps))
        x, y = 0, 0
        if manual:
            layers, x, y = _manual_image_layers(index, cue, info)
            graph.extend(layers)
        elif is_video:
            graph.append(
                f"[{index}:v]setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps={fps:g},format=yuv420p,"
                f"trim=duration={cue['duration']},"
                f"setpts=PTS-STARTPTS+{cue['start']}/TB[cut{index}]")
        elif manual or cue.get("credit"):
            # Preserve the entire uploaded picture. Legacy animated photos
            # keep their small zoom margin; manual images stay stationary.
            margin = 1.0 if manual else .9
            fit_width = max(2, int(width * margin) // 2 * 2)
            fit_height = max(2, int(height * margin) // 2 * 2)
            graph.extend([
                f"[{index}:v]split=2[bg{index}][fg{index}]",
                f"[bg{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},gblur=sigma=24[blur{index}]",
                f"[fg{index}]scale={fit_width}:{fit_height}:force_original_aspect_ratio=decrease[fit{index}]",
                f"[blur{index}][fit{index}]overlay=(W-w)/2:(H-h)/2"
                + (":format=auto" if manual else "") + f",setsar=1[photo{index}]",
            ])
        else:
            graph.append(
                f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1[photo{index}]")
        if not is_video and not manual:
            # Pin the pixel format: some FFmpeg builds crash when zoompan
            # negotiates a JPEG/overlay's full-range YUV format implicitly.
            motion = (f"fps={fps:g}," if manual else
                      f"zoompan=z='min(1+0.04*on/{frames},1.04)':"
                      f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
                      f"d=1:s={width}x{height}:fps={fps:g},format=yuv420p,")
            pixel_format = "yuva420p" if manual else "yuv420p"
            graph.append(f"[photo{index}]format={pixel_format}," + motion +
                         f"trim=duration={cue['duration']},"
                         f"setpts=PTS-STARTPTS+{cue['start']}/TB[cut{index}]")
        graph.append(
            f"[{previous}][cut{index}]overlay=x='{x}':y='{y}':"
            f"enable='gte(t,{cue['start']})*lt(t,{cue['end']})':"
            f"eof_action=pass:repeatlast=0[v{index}]")
        previous = f"v{index}"
    if audio_layers:
        if _has_audio(input_path):
            graph.append("[0:a]asetpts=PTS-STARTPTS,aresample=48000[sourceaudio]")
            audio_layers.insert(0, "[sourceaudio]")
        graph.append("".join(audio_layers) + f"amix=inputs={len(audio_layers)}:duration=longest:"
                     f"dropout_transition=0:normalize=0,apad,atrim=duration={duration:g}[mixedaudio]")
    command.extend(["-filter_complex_threads", "1", "-filter_complex", ";".join(graph),
                    "-map", f"[{previous}]", "-map", "[mixedaudio]" if audio_layers else "0:a?", "-t", f"{duration:g}",
                    *video_encode_args(QUALITY_FAST), "-pix_fmt", "yuv420p",
                    "-c:a", "aac" if audio_layers else "copy", "-movflags", "+faststart", *METADATA_SCRUB, output_path])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except Exception:
        Path(output_path).unlink(missing_ok=True)
        raise
    if result.returncode != 0 or not Path(output_path).is_file() or Path(output_path).stat().st_size == 0:
        Path(output_path).unlink(missing_ok=True)
        raise RuntimeError(f"B-roll render failed (ffmpeg exit {result.returncode})")
    import layout_ranges
    ranges = cutaway_layouts(layout_ranges.read(input_path), cues)
    layout_ranges.write(output_path, [(r["start"], r["end"], r["layout"], r.get("content"))
                                      for r in ranges])
