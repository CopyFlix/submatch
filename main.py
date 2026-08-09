from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import re
from rapidfuzz import fuzz

app = FastAPI(title="Torrentio & OpenSubtitles Matcher API")

# ==========================================
# 1. استخراج البيانات الوصفية (Metadata)
# ==========================================
def extract_metadata(filename, raw_item=None):
    if not raw_item:
        raw_item = {}
    name_lower = (filename or "").lower()
    meta = {"res": None, "source": None, "group": None, "ep": None}

    # استخراج رقم الحلقة (مهم جداً للأنمي والمسلسلات)
    ep_match = re.search(r'[s]\d{1,2}[e](\d{1,3})\b', name_lower) or \
               re.search(r'\b\d{1,2}x(\d{1,3})\b', name_lower) or \
               re.search(r'-\s*0*(\d{1,3})\b', name_lower) or \
               re.search(r'\be0*(\d{1,3})\b', name_lower) or \
               re.search(r'episode\s*0*(\d{1,3})\b', name_lower)
    
    if ep_match:
        meta["ep"] = int(ep_match.group(1))

    # المصدر
    if re.search(r'bluray|bdrip|remux|\[bd\]', name_lower): meta["source"] = 'bluray'
    elif re.search(r'web-dl|webrip|web|amzn|crunchyroll|shahid', name_lower): meta["source"] = 'web'
    elif re.search(r'dvdrip|dvdscr|dvd', name_lower): meta["source"] = 'dvd'

    # فريق الرفع
    group_match = re.search(r'^\[([^\]]+)\]', filename) or re.search(r'-([a-zA-Z0-9_]+)(?:\.\w{2,4})?$', filename)
    if group_match:
        meta["group"] = group_match.group(1).lower().strip()

    return meta

def extract_clean_text(text, ignore_title=""):
    if not text: return ""
    text = text.lower()
    
    # تنظيف الكلمات التي تخدع RapidFuzz
    stop_words = r'👤\s*\d+|💾\s*[\d\.]+\s*[g|m]b|⚙️\s*\w+|multi audio|torrentio|1080p|720p|2160p|4k|hevc|x265|x264|dual audio|mkv|mp4|srt|ass'
    text = re.sub(stop_words, '', text)
    text = re.sub(r'[._\-\[\]\(\)\n\/]', ' ', text)
    
    if ignore_title:
        clean_title = re.sub(r'[._\-\[\]\(\)]', ' ', ignore_title.lower())
        for word in clean_title.split():
            if len(word) > 2:
                text = text.replace(word, '')
                
    return re.sub(r'\s+', ' ', text).strip()

# ==========================================
# 2. معالجة الـ JSON
# ==========================================
def parse_streams(data, ignore_title=""):
    streams = []
    streams_data = data.get("streams", data) if isinstance(data, dict) else data
    if not isinstance(streams_data, list): return []
        
    for item in streams_data:
        if not isinstance(item, dict): continue
        filename = item.get("behaviorHints", {}).get("filename", "")
        if not filename:
            title_lines = item.get("title", "").split("\n")
            filename = title_lines[0] if title_lines else item.get("name", "")
            
        streams.append({
            "original_name": filename,
            "meta": extract_metadata(filename, item),
            "clean_tags": extract_clean_text(filename, ignore_title)
        })
    return streams

def parse_subtitles(data, ignore_title=""):
    subtitles = []
    subs_data = data.get("subtitles", data) if isinstance(data, dict) else data
    if not isinstance(subs_data, list): return []
        
    for item in subs_data:
        if not isinstance(item, dict): continue
        sub_name = item.get("SubFileName") or item.get("MovieReleaseName") or ""
        subtitles.append({
            "sub_id": item.get("IDSubtitleFile") or item.get("IDSubtitle"),
            "original_name": sub_name,
            "meta": extract_metadata(sub_name, item),
            "clean_tags": extract_clean_text(sub_name, ignore_title)
        })
    return subtitles

# ==========================================
# 3. حساب السكور باستخدام RapidFuzz + Metadata
# ==========================================
def calculate_score(stream, sub):
    meta1, meta2 = stream["meta"], sub["meta"]

    # 1. الفلتر القاتل: إذا اختلف رقم الحلقة، السكور صفر (يمنع أخطاء الأنمي)
    if meta1["ep"] is not None and meta2["ep"] is not None:
        if meta1["ep"] != meta2["ep"]:
            return -1000

    # 2. استخدام WRatio من RapidFuzz (يدمج السورت والسيت بذكاء شديد)
    base_score = fuzz.WRatio(stream["clean_tags"], sub["clean_tags"])

    # 3. مكافآت وعقوبات Metadata
    if meta1["group"] and meta2["group"]:
        if meta1["group"] == meta2["group"]:
            base_score += 30  # نفس فريق الرفع
        else:
            base_score -= 15  # فرق رفع مختلفة

    if meta1["source"] and meta2["source"]:
        if meta1["source"] == meta2["source"]:
            base_score += 20
        else:
            base_score -= 25

    return base_score

# ==========================================
# 4. مسارات الـ API (Endpoints)
# ==========================================
@app.post("/api/match")
async def match_subtitles(request: Request):
    try:
        body = await request.json()
        t_data = body.get("torrentio_json", {})
        s_data = body.get("opensubtitles_json", {})
        movie_title = body.get("movie_title", "")

        streams = parse_streams(t_data, movie_title)
        subtitles = parse_subtitles(s_data, movie_title)

        if not streams or not subtitles:
            return JSONResponse({"error": "No valid streams or subtitles found."}, status_code=400)

        best_overall_score = -9999
        best_pair = None
        matrix_results = []

        for stream in streams:
            best_sub_for_stream = None
            max_score_for_stream = -9999

            for sub in subtitles:
                score = calculate_score(stream, sub)
                
                if score > max_score_for_stream:
                    max_score_for_stream = score
                    best_sub_for_stream = sub
                    
                if score > best_overall_score:
                    best_overall_score = score
                    best_pair = (stream, sub)

            matrix_results.append({
                "stream_name": stream["original_name"],
                "best_subtitle": best_sub_for_stream["original_name"] if best_sub_for_stream else "None",
                "sub_id": best_sub_for_stream["sub_id"] if best_sub_for_stream else "-",
                "score": round(max_score_for_stream, 2)
            })

        return {
            "success": True,
            "overall_best_match": {
                "stream": best_pair[0]["original_name"],
                "subtitle": best_pair[1]["original_name"],
                "sub_id": best_pair[1]["sub_id"],
                "score": round(best_overall_score, 2)
            },
            "matrix_results": matrix_results
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/")
def home():
    return {"message": "RapidFuzz API is Running. Send POST to /api/match"}