from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Union, List, Dict, Any, Optional
import json
import re
from rapidfuzz import fuzz

app = FastAPI(
    title="Torrentio & OpenSubtitles Matcher API",
    description="API لمطابقة جودات الأفلام مع الترجمات بنفس الخوارزميات تماماً"
)

# --- دالة الاستخراج والتنظيف الذكي (نفس الكود الأصلي 100%) ---
def extract_clean_text(text: str, ignore_title: str = "") -> str:
    if not text:
        return ""
    text = text.lower()
    
    # تنظيف العبارات الشائعة التي لا فائدة منها في Stremio
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

def parse_torrentio_streams(data, ignore_title=""):
    streams = []
    if isinstance(data, dict) and "streams" in data:
        data = data["streams"]
    elif not isinstance(data, list):
        return []
        
    for item in data:
        if not isinstance(item, dict):
            continue
        # محاولة أخذ الاسم المباشر من behaviorHints أو title
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
    if not isinstance(data, list):
        return []
        
    for item in data:
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


# --- نماذج البيانات المجمعة (Request Models) ---
class MatchRequest(BaseModel):
    movie_title: Optional[str] = ""
    torrentio_json: Union[Dict[str, Any], List[Any], str]
    opensubtitles_json: Union[List[Dict[str, Any]], str]


@app.post("/match")
def match_subtitles(payload: MatchRequest):
    # معالجة المدخلات سواء كانت كود JSON نصي أو JSON كـ Object/List مباشر
    t_data = payload.torrentio_json
    if isinstance(t_data, str):
        try:
            t_data = json.loads(t_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"خطأ في صيغة torrentio_json: {str(e)}")
            
    s_data = payload.opensubtitles_json
    if isinstance(s_data, str):
        try:
            s_data = json.loads(s_data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"خطأ في صيغة opensubtitles_json: {str(e)}")

    movie_title = payload.movie_title or ""
    
    # تنفيذ الاستخراج والتحليل بنفس طريقة Streamlit
    streams = parse_torrentio_streams(t_data, movie_title)
    subtitles = parse_opensubtitles(s_data, movie_title)

    if not streams or not subtitles:
        raise HTTPException(
            status_code=400, 
            detail=" تعذر استخراج البيانات. تأكد من أن الـ JSON مكتوب بشكل صحيح ويحتوي على عناصر قابلة للتحليل."
        )

    best_overall_score = -1
    best_pair = None
    matrix_results = []

    for stream in streams:
        best_sub_for_stream = None
        max_score_for_stream = -1

        for sub in subtitles:
            # استخدام نفس الخوارزمية (fuzz.token_set_ratio)
            score = fuzz.token_set_ratio(stream["clean_tags"], sub["clean_tags"])

            if score > max_score_for_stream:
                max_score_for_stream = score
                best_sub_for_stream = sub

            if score > best_overall_score:
                best_overall_score = score
                best_pair = (stream, sub)

        matrix_results.append({
            "نسخة الفيديو (Torrentio)": stream["original_name"],
            "أفضل ترجمة متطابقة": best_sub_for_stream["original_name"] if best_sub_for_stream else None,
            "ID الترجمة": best_sub_for_stream["sub_id"] if best_sub_for_stream else None,
            "نسبة التوافق": f"{max_score_for_stream}%",
            "score_numeric": max_score_for_stream
        })

    # إرجاع النتيجة بالكامل
    return {
        "status": "success",
        "total_streams": len(streams),
        "total_subtitles": len(subtitles),
        "best_overall_match": {
            "torrentio_stream": best_pair[0]["original_name"] if best_pair else None,
            "opensubtitles_file": best_pair[1]["original_name"] if best_pair else None,
            "sub_id": best_pair[1]["sub_id"] if best_pair else None,
            "compatibility_score": f"{best_overall_score}%",
            "score_numeric": best_overall_score
        },
        "matrix_results": matrix_results
    }
