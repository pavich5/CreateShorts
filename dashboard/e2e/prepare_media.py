"""Extract real source-derived 9:16 media fixtures inside the backend container."""
import json
from pathlib import Path
import subprocess
import sys

directory = Path(sys.argv[1])
job = json.loads((directory / "job.json").read_text())
source = Path("uploads") / (job["jobId"] + "_" + Path(job["source"]).name)
if not source.is_file():
    raise SystemExit("The original required uploaded source is no longer available.")
common = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
crop = "crop=ih*9/16:ih,scale=180:320"
subprocess.run(common + ["-ss", "10", "-i", str(source), "-t", "6", "-vf", crop,
                         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                         str(directory / "source-overlay.mp4")], check=True)
subprocess.run(common + ["-ss", "20", "-i", str(source), "-t", "6", "-vn", "-c:a", "pcm_s16le",
                         str(directory / "source-audio.wav")], check=True)
subprocess.run(common + ["-ss", "10", "-i", str(source), "-frames:v", "1", "-vf", crop,
                         str(directory / "source-image.jpg")], check=True)
