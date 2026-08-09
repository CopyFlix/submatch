from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
import re
from rapidfuzz import fuzz

app = FastAPI(title="Torrentio & OpenSubtitles Matcher API")

# ==========================================
# 1. التنظيف بنفس طريقة Streamlit المحلية تماماً
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
# 2. استخراج الحلقات لحماية المسلسلات فقط
# ==========================================
def extract_episode(filename):
    name_lower = (filename or "").lower()
    ep_match = re.search(r'[s]\d{1,2}[e](\d{1,3})\b', name_lower) or \
               re.search(r'\b\d{1,2}x(\d{1,3})\b', name_lower) or \
               re.search(r'-\s*0*(\d{1,3})\b', name_lower)
    return int(ep_match.group(1)) if ep_match else None

# ==========================================
# 3. Parsing JSON
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
            "ep": extract_episode(filename),
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
            "ep": extract_episode(sub_name),
            "clean_tags": extract_clean_text(sub_name, ignore_title)
        })
    return subtitles

# ==========================================
# 4. نفس منطق Streamlit (token_set_ratio)
# ==========================================
def calculate_score(stream, sub):
    # حماية المسلسلات: إذا كان كلاهما يحتوي على رقم حلقة مختلف، يتم الاستبعاد
    if stream["ep"] is not None and sub["ep"] is not None:
        if stream["ep"] != sub["ep"]:
            return -1000

    # استخدام نفس الدالة المحلية 100%
    return fuzz.token_set_ratio(stream["clean_tags"], sub["clean_tags"])

# ==========================================
# 5. Endpoints
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

        best_overall_score = -1
        best_pair = None
        matrix_results = []

        for stream in streams:
            best_sub_for_stream = None
            max_score_for_stream = -1

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
                "score": f"{round(max_score_for_stream, 2)}%"
            })

        return {
            "success": True,
            "overall_best_match": {
                "stream": best_pair[0]["original_name"],
                "subtitle": best_pair[1]["original_name"],
                "sub_id": best_pair[1]["sub_id"],
                "score": f"{round(best_overall_score, 2)}%"
            },
            "matrix_results": matrix_results
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/")
def home():
    return HTMLResponse("<h1>🚀 Matching API (Streamlit Logic Exact Mirror)</h1>")
