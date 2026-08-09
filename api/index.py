from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import re
from rapidfuzz import fuzz

app = FastAPI(title="Torrentio & OpenSubtitles Matcher API")

# ==========================================
# 1. دالة الاستخراج والتنظيف الذكي
# ==========================================
def extract_clean_text(text, ignore_title=""):
    if not text:
        return ""
    text = text.lower()
    
    # تنظيف العبارات الشائعة التي لا فائدة منها
    text = re.sub(r'👤\s*\d+|💾\s*[\d\.]+\s*[g|m]b|⚙️\s*\w+|multi audio|torrentio', '', text)
    
    # تحويل الرموز لمسافات
    text = re.sub(r'[._\-\[\]\(\)\n\/]', ' ', text)
    
    # إزالة اسم الفيلم/المسلسل إذا تم تزويده
    if ignore_title:
        clean_title = re.sub(r'[._\-\[\]\(\)]', ' ', ignore_title.lower())
        for word in clean_title.split():
            if len(word) > 2:
                text = text.replace(word, '')
                
    return re.sub(r'\s+', ' ', text).strip()

# ==========================================
# 2. تحليل البيانات
# ==========================================
def parse_torrentio_streams(data, ignore_title=""):
    streams = []
    streams_data = data.get("streams", data) if isinstance(data, dict) else data
    if not isinstance(streams_data, list):
        return []
        
    for item in streams_data:
        if not isinstance(item, dict):
            continue
        filename = item.get("behaviorHints", {}).get("filename", "")
        if not filename:
            title_lines = item.get("title", "").split("\n")
            filename = title_lines[0] if title_lines else item.get("name", "")
            
        clean_tags = extract_clean_text(filename, ignore_title)
        streams.append({
            "original_name": filename,
            "raw_item": item,
            "clean_tags": clean_tags
        })
    return streams

def parse_opensubtitles(data, ignore_title=""):
    subtitles = []
    subs_data = data.get("subtitles", data) if isinstance(data, dict) else data
    if not isinstance(subs_data, list):
        return []
        
    for item in subs_data:
        if not isinstance(item, dict):
            continue
        sub_name = item.get("SubFileName") or item.get("MovieReleaseName") or ""
        clean_tags = extract_clean_text(sub_name, ignore_title)
        subtitles.append({
            "sub_id": item.get("IDSubtitleFile") or item.get("IDSubtitle"),
            "original_name": sub_name,
            "raw_item": item,
            "clean_tags": clean_tags
        })
    return subtitles

# ==========================================
# 3. المسارات والحساب والترتيب
# ==========================================
@app.post("/api/match")
@app.post("/match")
@app.post("/")
async def match_subtitles(request: Request):
    try:
        body = await request.json()
        t_data = body.get("torrentio_json", {})
        s_data = body.get("opensubtitles_json", {})
        movie_title = body.get("movie_title", "")

        streams = parse_torrentio_streams(t_data, movie_title)
        subtitles = parse_opensubtitles(s_data, movie_title)

        if not streams or not subtitles:
            return JSONResponse({"error": "تعذر استخراج البيانات. تأكد من أن الـ JSON مكتوب بشكل صحيح ويحتوي على مصفوفات."}, status_code=400)

        best_overall_score = -1
        best_pair = None
        raw_matrix = []

        for stream in streams:
            best_sub_for_stream = None
            max_score_for_stream = -1

            for sub in subtitles:
                # المقارنة المباشرة
                score = fuzz.token_set_ratio(stream["clean_tags"], sub["clean_tags"])
                
                if score > max_score_for_stream:
                    max_score_for_stream = score
                    best_sub_for_stream = sub
                    
                if score > best_overall_score:
                    best_overall_score = score
                    best_pair = (stream, sub)

            raw_matrix.append({
                "stream_name": stream["original_name"],
                "best_subtitle": best_sub_for_stream["original_name"] if best_sub_for_stream else "غير متوفر",
                "sub_id": best_sub_for_stream["sub_id"] if best_sub_for_stream else "-",
                "raw_score": max_score_for_stream,
                "score": f"{max_score_for_stream}%"
            })

        # 🎯 ترتيب النتائج تنازلياً حسب نسبة التوافق لتمثيل نفس جدول Streamlit تماماً
        raw_matrix.sort(key=lambda x: x["raw_score"], reverse=True)

        # تنظيف النتائج وتجهيز الـ JSON النهائي
        matrix_results = [
            {
                "stream_name": item["stream_name"],
                "best_subtitle": item["best_subtitle"],
                "sub_id": item["sub_id"],
                "score": item["score"]
            }
            for item in raw_matrix
        ]

        return {
            "success": True,
            "total_streams": len(streams),
            "total_subtitles": len(subtitles),
            "overall_best_match": {
                "stream": best_pair[0]["original_name"],
                "subtitle": best_pair[1]["original_name"],
                "sub_id": best_pair[1]["sub_id"],
                "score": f"{best_overall_score}%"
            },
            "matrix_results": matrix_results
        }

    except Exception as e:
        return JSONResponse({"error": f"خطأ في معالجة البيانات: {str(e)}"}, status_code=500)

@app.get("/")
def home():
    return {"status": "online", "message": "Exact Mirror of Streamlit Matcher API"}
