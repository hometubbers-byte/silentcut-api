from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess, os, uuid, tempfile, shutil

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "/tmp/silentcut"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/")
def root():
    return {"status": "SilentCut API running ✅"}

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/process")
async def process_video(
    file: UploadFile = File(...),
    silence_thresh: float = Form(-35.0),
    silence_duration: float = Form(0.5),
    mode: str = Form("auto")
):
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(UPLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    input_path  = os.path.join(job_dir, "input.mp4")
    output_path = os.path.join(job_dir, "output.mp4")
    segments_path = os.path.join(job_dir, "segments.txt")

    try:
        # Save uploaded file
        with open(input_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Detect silence using ffmpeg silencedetect
        detect_cmd = [
            "ffmpeg", "-i", input_path,
            "-af", f"silencedetect=noise={silence_thresh}dB:d={silence_duration}",
            "-f", "null", "-"
        ]
        result = subprocess.run(detect_cmd, capture_output=True, text=True)
        stderr = result.stderr

        # Parse silence intervals
        silence_starts, silence_ends = [], []
        for line in stderr.split("\n"):
            if "silence_start" in line:
                try: silence_starts.append(float(line.split("silence_start: ")[1].split()[0]))
                except: pass
            if "silence_end" in line:
                try: silence_ends.append(float(line.split("silence_end: ")[1].split("|")[0].strip()))
                except: pass

        # Get video duration
        dur_cmd = ["ffprobe","-v","quiet","-show_entries","format=duration","-of","csv=p=0", input_path]
        dur_result = subprocess.run(dur_cmd, capture_output=True, text=True)
        try: duration = float(dur_result.stdout.strip())
        except: duration = 60.0

        # Build keep segments (non-silent parts)
        keep_segments = []
        prev = 0.0
        for start, end in zip(silence_starts, silence_ends):
            if start > prev + 0.1:
                keep_segments.append((prev, start))
            prev = end
        if prev < duration - 0.1:
            keep_segments.append((prev, duration))

        if not keep_segments:
            keep_segments = [(0, duration)]

        # Build FFmpeg filter for cutting
        filter_parts = []
        concat_v, concat_a = "", ""
        for i, (s, e) in enumerate(keep_segments):
            filter_parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
            filter_parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
            concat_v += f"[v{i}]"
            concat_a += f"[a{i}]"

        n = len(keep_segments)
        filter_parts.append(f"{concat_v}{concat_a}concat=n={n}:v=1:a=1[outv][outa]")
        filter_complex = ";".join(filter_parts)

        # Run FFmpeg cut
        cut_cmd = [
            "ffmpeg", "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-y", output_path
        ]
        cut_result = subprocess.run(cut_cmd, capture_output=True, text=True, timeout=300)

        if not os.path.exists(output_path):
            return JSONResponse({"error": "فشل في معالجة الفيديو", "details": cut_result.stderr[-500:]}, status_code=500)

        cut_duration = duration - sum(e-s for s,e in zip(silence_starts, silence_ends))
        saved = duration - cut_duration

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename="silentcut_output.mp4",
            headers={
                "X-Original-Duration": str(round(duration, 2)),
                "X-Cut-Duration": str(round(cut_duration, 2)),
                "X-Saved-Seconds": str(round(saved, 2)),
                "X-Segments-Cut": str(len(silence_starts)),
            }
        )

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "انتهت مهلة المعالجة — الفيديو كبير جداً"}, status_code=408)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        # Cleanup after 5 min (background)
        pass

@app.get("/detect")
async def detect_only():
    return {"message": "استخدم POST /process لرفع الفيديو"}
