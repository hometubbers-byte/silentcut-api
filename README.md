# SilentCut API — سيرفر القص الحقيقي

## النشر على Render.com (مجاناً)

1. ارفع هذا المجلد على GitHub
2. اذهب إلى render.com وسجّل حساباً
3. اضغط "New Web Service"
4. اربطه بـ GitHub repo
5. الإعدادات:
   - Build Command: `apt-get update && apt-get install -y ffmpeg && pip install -r requirements.txt`
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Plan: Free
6. اضغط Deploy
7. انسخ رابط السيرفر (مثال: https://silentcut-api.onrender.com)
8. ضعه في ملف silentcut_final.html

## API

POST /process
- file: ملف الفيديو
- silence_thresh: حساسية الكشف (افتراضي: -35)
- silence_duration: أقل مدة للصمت بالثواني (افتراضي: 0.5)

GET /health — للتحقق من أن السيرفر يعمل
