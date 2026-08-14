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
            # Score EVERY subtitle against this stream (this part was already a real matrix — the
            # old code just threw every row but the single best one away before ever returning it).
            scored_subs = []
            for sub in subtitles:
                score = fuzz.token_set_ratio(stream["clean_tags"], sub["clean_tags"])
                scored_subs.append((score, sub))
            scored_subs.sort(key=lambda pair: pair[0], reverse=True)

            max_score_for_stream, best_sub_for_stream = (
                scored_subs[0] if scored_subs else (-1, None)
            )

            if max_score_for_stream > best_overall_score:
                best_overall_score = max_score_for_stream
                best_pair = (stream, best_sub_for_stream)

            raw_matrix.append({
                "stream_name": stream["original_name"],
                "best_subtitle": best_sub_for_stream["original_name"] if best_sub_for_stream else "غير متوفر",
                "sub_id": best_sub_for_stream["sub_id"] if best_sub_for_stream else "-",
                "raw_score": max_score_for_stream,
                "score": f"{max_score_for_stream}%",
                # NEW — every subtitle's own score against THIS stream, not just the winner. This is
                # what actually makes the response a matrix: a client caring about one specific
                # stream (its own selected torrent) can now see a real score for every candidate
                # subtitle against it, instead of only ever learning about the single best one.
                "all_matches": [
                    {
                        "sub_id": sub["sub_id"],
                        "subtitle_name": sub["original_name"],
                        "score": f"{score}%"
                    }
                    for score, sub in scored_subs
                ]
            })

        # 🎯 ترتيب النتائج تنازلياً حسب نسبة التوافق لتمثيل نفس جدول Streamlit تماماً
        raw_matrix.sort(key=lambda x: x["raw_score"], reverse=True)

        # تنظيف النتائج وتجهيز الـ JSON النهائي — كل الحقول القديمة باقية بدون أي تغيير (أي عميل
        # تاني بيقرأ الشكل القديم لسه شغال زي ما هو)، all_matches حقل إضافي بس.
        matrix_results = [
            {
                "stream_name": item["stream_name"],
                "best_subtitle": item["best_subtitle"],
                "sub_id": item["sub_id"],
                "score": item["score"],
                "all_matches": item["all_matches"]
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
