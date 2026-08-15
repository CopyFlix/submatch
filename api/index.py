from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import re
import numpy as np
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


# ==========================================
# 4. محاذاة توقيت الترجمة (language-agnostic subtitle-to-subtitle sync)
# ==========================================
# لا علاقة له بـ /api/match أعلاه ولا يغيّر منطقه — endpoint مستقل تماماً. الفكرة: بدل تحليل صوت
# الفيديو (ثقيل ويحتاج تحميل الملف نفسه)، نأخذ توقيت ترجمة أخرى (بأي لغة) ثبت أصلاً أنها مطابقة
# لنفس نسخة التورنت (sameRelease/أعلى score من /api/match)، ونحاذي توقيت الترجمة العربية عليها عبر
# مقارنة "إشارة نشاط" (متى يظهر أي سطر) بين الاثنين — لا نص، فقط أرقام start_ms/end_ms، فهذا يعمل
# بغض النظر عن اللغة. يغطي الحالتين الأكثر شيوعاً: إزاحة ثابتة (انترو/مقدمة مختلفة الطول) وفرق
# معدل إطارات (23.976 مقابل 25fps) كتمدد خطي. لا يغطي حالة مشاهد محذوفة/مُعاد ترتيبها فعلياً بين
# النسختين — الـ confidence المُرجعة مصممة خصيصاً ليكتشف العميل هذه الحالة ويتجاهل النتيجة بدل
# تطبيق محاذاة خاطئة.

class Cue(BaseModel):
    start_ms: float
    end_ms: float


class AlignRequest(BaseModel):
    # توقيت الترجمة المرجعية (المؤكد تطابقها مع نسخة التورنت المختارة، أي لغة كانت)
    reference_cues: list[Cue]
    # توقيت الترجمة العربية المطلوب إعادة محاذاتها
    target_cues: list[Cue]


class AlignResponse(BaseModel):
    success: bool
    offset_ms: float = 0.0
    scale: float = 1.0
    # نسبة تداخل إشارة النشاط بعد تطبيق التحويل — 1.0 مطابقة كاملة، قريب من 0 يعني "لا تثق بهذه
    # النتيجة إطلاقاً" (يجب على العميل تجاهلها والإبقاء على التوقيت الأصلي في هذه الحالة).
    confidence: float = 0.0
    error: Optional[str] = None


_BIN_MS = 40.0  # دقة أخذ العينات — كافية لأطوال أسطر الحوار المعتادة وسريعة بما يكفي
_SCALE_CANDIDATES = [
    1.0,
    24 / 23.976, 23.976 / 24,
    25 / 23.976, 23.976 / 25,
    25 / 24, 24 / 25,
    30 / 29.97, 29.97 / 30,
]
_MAX_OFFSET_SEARCH_MS = 5 * 60 * 1000.0  # لا نبحث عن إزاحة أكبر من 5 دقائق في أي اتجاه


def _rasterize(cues: list[Cue], scale: float, length_bins: int) -> np.ndarray:
    signal = np.zeros(length_bins, dtype=np.float32)
    for cue in cues:
        start_bin = max(0, int((cue.start_ms * scale) / _BIN_MS))
        end_bin = min(length_bins, int((cue.end_ms * scale) / _BIN_MS) + 1)
        if end_bin > start_bin:
            signal[start_bin:end_bin] = 1.0
    return signal


def _fft_correlate_full(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # Same result as np.correlate(a, b, mode="full") but via FFT convolution — O(n log n) instead
    # of np.correlate's naive O(n*m), which is what actually caused a real 2-hour-movie request
    # (~180k bins at _BIN_MS resolution, x9 scale candidates) to time out client-side at 30s. The
    # identity used: correlate(a, b, full) == convolve(a, reverse(b), full).
    n = len(a) + len(b) - 1
    fft_size = 1 << (n - 1).bit_length()
    fa = np.fft.rfft(a, fft_size)
    fb = np.fft.rfft(b[::-1], fft_size)
    return np.fft.irfft(fa * fb, fft_size)[:n]


def _best_offset_bins(reference: np.ndarray, target: np.ndarray) -> tuple[int, float]:
    correlation = _fft_correlate_full(reference, target)
    max_offset_bins = int(_MAX_OFFSET_SEARCH_MS / _BIN_MS)
    center = len(target) - 1
    lo = max(0, center - max_offset_bins)
    hi = min(len(correlation), center + max_offset_bins + 1)
    window = correlation[lo:hi]
    if window.size == 0:
        return 0, 0.0
    best_idx = int(np.argmax(window)) + lo
    shift_bins = best_idx - center  # موجب => يجب تأخير الترجمة الهدف لتحاذي المرجع
    ref_active = float(np.sum(reference))
    confidence = float(correlation[best_idx]) / ref_active if ref_active > 0 else 0.0
    return shift_bins, min(confidence, 1.0)


def align_cues(req: AlignRequest) -> AlignResponse:
    if not req.reference_cues or not req.target_cues:
        return AlignResponse(success=False, error="reference_cues and target_cues must both be non-empty")

    ref_end = max(c.end_ms for c in req.reference_cues)
    tgt_end = max(c.end_ms for c in req.target_cues)

    best_result: Optional[AlignResponse] = None
    for scale in _SCALE_CANDIDATES:
        length_ms = max(ref_end, tgt_end * scale) + _MAX_OFFSET_SEARCH_MS
        length_bins = int(length_ms / _BIN_MS) + 1
        reference_signal = _rasterize(req.reference_cues, 1.0, length_bins)
        target_signal = _rasterize(req.target_cues, scale, length_bins)

        shift_bins, confidence = _best_offset_bins(reference_signal, target_signal)
        offset_ms = shift_bins * _BIN_MS

        candidate = AlignResponse(success=True, offset_ms=offset_ms, scale=scale, confidence=confidence)
        if best_result is None or candidate.confidence > best_result.confidence:
            best_result = candidate

    assert best_result is not None
    return best_result


@app.post("/api/align", response_model=AlignResponse)
async def align_subtitles(request: AlignRequest = Body(...)):
    try:
        return align_cues(request)
    except Exception as e:
        return AlignResponse(success=False, error=str(e))


@app.get("/")
def home():
    return {"status": "online", "message": "Exact Mirror of Streamlit Matcher API"}
