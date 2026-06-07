from fastapi import FastAPI, File, UploadFile, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess, os, uuid, asyncio

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

def cleanup(job_dir):
    import shutil, time
    time.sleep(300)
    try: shutil.rmtree(job_dir)
    except: pass

@app.post("/process")
async def process_video(
    background_tasks: BackgroundTasks,
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

    try:
        # Save uploaded file in chunks — يدعم الملفات الكبيرة
        with open(input_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                f.write(chunk)

        file_size_mb = os.path.getsize(input_path) / (1024 * 1024)

        # Get video duration
        dur_cmd = ["ffprobe","-v","quiet","-show_entries",
                   "format=duration","-of","csv=p=0", input_path]
        dur_result = subprocess.run(dur_cmd, capture_output=True, text=True)
        try: duration = float(dur_result.stdout.strip())
        except: duration = 60.0

        # Detect silence
        detect_cmd = [
            "ffmpeg", "-i", input_path,
            "-af", f"silencedetect=noise={silence_thresh}dB:d={silence_duration}",
            "-f", "null", "-"
        ]
        result = subprocess.run(detect_cmd, capture_output=True, text=True, timeout=600)
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

        # Build keep segments
        keep_segments = []
        prev = 0.0
        for start, end in zip(silence_starts, silence_ends):
            if start > prev + 0.1:
                keep_segments.append((round(prev, 3), round(start, 3)))
            prev = end
        if prev < duration - 0.1:
            keep_segments.append((round(prev, 3), round(duration, 3)))

        if not keep_segments:
            keep_segments = [(0, duration)]

        # إذا كان الفيديو طويلاً — نستخدم concat file method (أسرع وأستقر)
        if len(keep_segments) > 0:
            # كتابة ملف القطع
            segments_file = os.path.join(job_dir, "segments.txt")
            
            # نقص كل جزء على حدة ثم ندمجهم
            part_files = []
            for i, (s, e) in enumerate(keep_segments):
                part_path = os.path.join(job_dir, f"part_{i}.mp4")
                cut_cmd = [
                    "ffmpeg", "-i", input_path,
                    "-ss", str(s), "-to", str(e),
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac",
                    "-avoid_negative_ts", "1",
                    "-y", part_path
                ]
                subprocess.run(cut_cmd, capture_output=True, timeout=600)
                if os.path.exists(part_path):
                    part_files.append(part_path)

            if len(part_files) == 1:
                # جزء واحد فقط
                os.rename(part_files[0], output_path)
            elif len(part_files) > 1:
                # دمج الأجزاء
                with open(segments_file, "w") as f:
                    for pf in part_files:
                        f.write(f"file '{pf}'\n")
                
                merge_cmd = [
                    "ffmpeg",
                    "-f", "concat", "-safe", "0",
                    "-i", segments_file,
                    "-c", "copy",
                    "-y", output_path
                ]
                subprocess.run(merge_cmd, capture_output=True, timeout=600)

        if not os.path.exists(output_path):
            return JSONResponse({"error": "فشل في معالجة الفيديو"}, status_code=500)

        cut_secs = sum(e - s for s, e in zip(silence_starts, silence_ends))
        new_dur = max(0, duration - cut_secs)
        output_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        background_tasks.add_task(cleanup, job_dir)

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename="silentcut_output.mp4",
            headers={
                "X-Original-Duration": str(round(duration, 2)),
                "X-Cut-Duration": str(round(new_dur, 2)),
                "X-Saved-Seconds": str(round(cut_secs, 2)),
                "X-Segments-Cut": str(len(silence_starts)),
                "X-File-Size-MB": str(round(output_size_mb, 1)),
                "Access-Control-Expose-Headers": "X-Original-Duration,X-Cut-Duration,X-Saved-Seconds,X-Segments-Cut,X-File-Size-MB"
            }
        )

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "انتهت مهلة المعالجة — الفيديو طويل جداً، جرب تقليل مدته"}, status_code=408)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
