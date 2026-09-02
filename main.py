"""
Kisisel Organizer -- yeniden tasarlanmis arayuz (v2)

Ekranlar: Bugun / Odak / Spor / Takvim / Karne
Temalar : "Buz" (beyaz-mavi, acik) ve "Grafit + Kum" (koyu) -- ust sagdaki
          kontrast dugmesi ile degisir, secim cihazda saklanir.

Veri katmani (Firestore senkronu, kilometre taslari, tum hesaplama
fonksiyonlari) onceki surumden bire bir korunmustur; yalnizca arayuz
katmani yenilenmistir.

Framework: Flet -- Android APK: flet build apk
"""

import flet as ft

try:
    _SPORTS_TAB_ICON = ft.Icons.DIRECTIONS_RUN
except AttributeError:
    _SPORTS_TAB_ICON = ft.Icons.SPORTS
import time
import threading
import asyncio
import json
import random
import calendar as cal_module
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    requests = None


# ---------------------------------------------------------
# FIREBASE (FIRESTORE) BULUT SENKRONIZASYONU
# ---------------------------------------------------------
FIREBASE_API_KEY = "AIzaSyABXzDZX_r55AFAZrDuhmyWuXdypdDL1sE"
FIREBASE_PROJECT_ID = "charmelon-94eae"
FIRESTORE_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}"
    f"/databases/(default)/documents"
)
ACCOUNT_CODE_KEY = "account_code"
# Bazi cihazlarda client_storage.get/set sessizce gecikiyor veya basarisiz
# oluyor (Flet'in bilinen bir sorunu). Bu yuzden oturum icinde bellekte de
# ayrica tutuyoruz: client_storage basarisiz olsa BILE kullanici "Yeni kod
# uret"e bastiginda ekranda kodun gorunmemesi/hic bir sey olmamasi hissi
# yasanmasin -- en azindan uygulama acikken kod dogru calissin.
_ACCOUNT_CODE_CACHE = {"code": None}


def get_account_code(page: ft.Page):
    try:
        code = page.client_storage.get(ACCOUNT_CODE_KEY)
    except Exception:
        code = None
    if code:
        _ACCOUNT_CODE_CACHE["code"] = code
        return code
    return _ACCOUNT_CODE_CACHE["code"]


def set_account_code(page: ft.Page, code):
    _ACCOUNT_CODE_CACHE["code"] = code or None
    for attempt in range(3):
        try:
            page.client_storage.set(ACCOUNT_CODE_KEY, code or "")
            return
        except Exception:
            if attempt < 2:
                time.sleep(0.05)


def firestore_get_field(account_code: str, field_name: str):
    """Firestore'daki belirtilen alani ceker.
    Onemli: 404 (bu hesap kodu bulutta hic olusturulmamis) ile gercek bos
    veriyi ({}) birbirinden ayirmak icin 404/hata durumunda None donuyoruz.
    Cagiran taraf (sync_field_from_cloud) None gordugunde yerel veriyi
    ASLA silmiyor -- aksi halde var olmayan/yanlis yazilan bir kod girildiginde
    cihazdaki tum gecmis veri sessizce bos veriyle degistirilirdi."""
    if requests is None:
        return None
    url = f"{FIRESTORE_BASE}/accounts/{account_code}?key={FIREBASE_API_KEY}"
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            fields = data.get("fields", {})
            json_str = fields.get(field_name, {}).get("stringValue", "{}")
            return json.loads(json_str)
        return None
    except Exception:
        return None


def firestore_set_field(account_code: str, field_name: str, data: dict):
    if requests is None:
        return False
    url = (
        f"{FIRESTORE_BASE}/accounts/{account_code}"
        f"?key={FIREBASE_API_KEY}&updateMask.fieldPaths={field_name}"
    )
    body = {"fields": {field_name: {"stringValue": json.dumps(data)}}}
    try:
        resp = requests.patch(url, json=body, timeout=8)
        return resp.status_code in (200, 201)
    except Exception:
        return False


def generate_account_code():
    return f"{random.randint(0, 999999):06d}"


# ---------------------------------------------------------
# KALICI DEPOLAMA YARDIMCI FONKSIYONLARI
# ---------------------------------------------------------
HISTORY_KEY = "focus_history"  # client_storage'da saklanan anahtar (yerel onbellek)
SWIM_KEY = "swim_history"
GYM_KEY = "gym_history"


# Bellek onbellegi: ayni veri ayni oturumda tekrar tekrar okunmaz.
# Bulut (Firestore) yalnizca uygulama acilisinda, arka planda bir kez cekilir --
# arayuz asla ag beklemez.
_MEM_CACHE = {}
_SYNCED_FIELDS = set()


def _read_local(page: ft.Page, cache_key: str) -> dict:
    try:
        raw = page.client_storage.get(cache_key)
    except Exception:
        raw = None
    if raw:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _load_cached_or_remote(page: ft.Page, field_name: str, cache_key: str) -> dict:
    if cache_key in _MEM_CACHE:
        return _MEM_CACHE[cache_key]
    data = _read_local(page, cache_key)
    _MEM_CACHE[cache_key] = data
    return data


def sync_field_from_cloud(page: ft.Page, field_name: str, cache_key: str):
    """Buluttan bir alani ceker; yerelden farkliysa onbellegi gunceller.
    Yalnizca arka plan is parcasindan cagrilmalidir.
    Donus degeri UC durumlu: None = hesap kodu bulutta bulunamadi ya da aga
    erisilemedi (bu durumda yerel veriye ASLA dokunulmaz); False = bulundu
    ama yerelle ayniydi; True = bulundu ve yerel onbellek guncellendi."""
    code = get_account_code(page)
    if not code or field_name in _SYNCED_FIELDS:
        return None
    remote = firestore_get_field(code, field_name)
    _SYNCED_FIELDS.add(field_name)
    if remote is None or not isinstance(remote, dict):
        return None
    changed = _MEM_CACHE.get(cache_key) != remote
    _MEM_CACHE[cache_key] = remote
    try:
        page.client_storage.set(cache_key, json.dumps(remote))
    except Exception:
        pass
    return changed




def _save_cached_and_remote(page: ft.Page, field_name: str, cache_key: str, data: dict):
    _MEM_CACHE[cache_key] = data
    try:
        page.client_storage.set(cache_key, json.dumps(data))
    except Exception:
        pass
    code = get_account_code(page)
    if not code:
        return

    def push():
        try:
            firestore_set_field(code, field_name, data)
        except Exception:
            pass

    threading.Thread(target=push, daemon=True).start()


def load_history(page: ft.Page) -> dict:
    return _load_cached_or_remote(page, "history_json", HISTORY_KEY)


def save_history(page: ft.Page, history: dict):
    _save_cached_and_remote(page, "history_json", HISTORY_KEY, history)


def add_focus_seconds(page: ft.Page, date_str: str, seconds: float):
    if seconds <= 0:
        return
    history = load_history(page)
    history[date_str] = history.get(date_str, 0) + seconds
    save_history(page, history)


def compute_focus_streak(history: dict) -> int:
    """Ardisik odaklanma gunu sayisini hesaplar. Bugun henuz calisilmadiysa
    dunden geriye dogru sayar (bugunun serisi henuz bozulmus sayilmaz)."""
    day = datetime.now().date()
    if history.get(day.strftime("%Y-%m-%d"), 0) <= 0:
        day -= timedelta(days=1)
    streak = 0
    while history.get(day.strftime("%Y-%m-%d"), 0) > 0:
        streak += 1
        day -= timedelta(days=1)
    return streak


def load_swim_history(page: ft.Page) -> dict:
    return _load_cached_or_remote(page, "swim_json", SWIM_KEY)


def save_swim_history(page: ft.Page, history: dict):
    _save_cached_and_remote(page, "swim_json", SWIM_KEY, history)


def add_swim_seconds(page: ft.Page, date_str: str, seconds: float):
    if seconds <= 0:
        return
    history = load_swim_history(page)
    history[date_str] = history.get(date_str, 0) + seconds
    save_swim_history(page, history)


def set_swim_seconds_for_date(page: ft.Page, date_str: str, seconds: float):
    """Belirli bir gunun yuzme suresini (eklemek yerine) dogrudan gecersiz kilar --
    hatali girisleri duzeltmek icin."""
    history = load_swim_history(page)
    if seconds <= 0:
        history.pop(date_str, None)
    else:
        history[date_str] = seconds
    save_swim_history(page, history)


WEIGHT_KEY = "weight_history"
WEIGHT_GOAL_KEY = "weight_goal"


def load_weight_history(page: ft.Page) -> dict:
    return _load_cached_or_remote(page, "weight_json", WEIGHT_KEY)


def save_weight_history(page: ft.Page, history: dict):
    _save_cached_and_remote(page, "weight_json", WEIGHT_KEY, history)


def set_weight_entry(page: ft.Page, date_str: str, kg: float):
    history = load_weight_history(page)
    history[date_str] = kg
    save_weight_history(page, history)


def delete_weight_entry(page: ft.Page, date_str: str):
    history = load_weight_history(page)
    history.pop(date_str, None)
    save_weight_history(page, history)


def load_weight_goal(page: ft.Page) -> str:
    data = _load_cached_or_remote(page, "weight_goal_json", WEIGHT_GOAL_KEY)
    return data.get("direction") if isinstance(data, dict) else None


MATCH_KEY = "match_history"


def load_match_history(page: ft.Page) -> dict:
    return _load_cached_or_remote(page, "match_json", MATCH_KEY)


def save_match_history(page: ft.Page, history: dict):
    _save_cached_and_remote(page, "match_json", MATCH_KEY, history)


def add_match(page: ft.Page, date_str: str):
    history = load_match_history(page)
    if not isinstance(history, dict):
        history = {}
    history[date_str] = history.get(date_str, 0) + 1
    save_match_history(page, history)


def set_match_count_for_date(page: ft.Page, date_str: str, count: int):
    """Belirli bir gunun mac sayisini dogrudan gecersiz kilar -- hatali
    girisleri duzeltmek icin."""
    history = load_match_history(page)
    if not isinstance(history, dict):
        history = {}
    if count <= 0:
        history.pop(date_str, None)
    else:
        history[date_str] = count
    save_match_history(page, history)


# --- Kilometre taslari ---
MILESTONE_THRESHOLDS = {
    "focus_hours": [10, 25, 50, 100, 250, 500, 1000],
    "gym_sessions": [10, 25, 50, 100, 200],
    "swim_hours": [10, 25, 50, 100],
    "matches": [10, 25, 50, 100],
}


def load_celebrated_milestones(page: ft.Page):
    data = _load_cached_or_remote(page, "milestones_json", "celebrated_milestones")
    keys = data.get("keys") if isinstance(data, dict) else None
    return set(keys) if isinstance(keys, list) else set()


def save_celebrated_milestones(page: ft.Page, keys_set):
    _save_cached_and_remote(page, "milestones_json", "celebrated_milestones", {"keys": list(keys_set)})


def compute_alltime_stats(page: ft.Page):
    try:
        focus_history = load_history(page)
        total_focus_hours = sum(v for v in focus_history.values() if isinstance(v, (int, float))) / 3600
    except Exception:
        total_focus_hours = 0

    try:
        gym_history = load_gym_history(page)
        total_gym_sessions = 0
        if isinstance(gym_history, dict):
            for week_key, done_map in gym_history.items():
                if not isinstance(done_map, dict):
                    continue
                for i in range(4):
                    if get_gym_entry(done_map, i)["done"]:
                        total_gym_sessions += 1
    except Exception:
        total_gym_sessions = 0

    try:
        swim_history = load_swim_history(page)
        total_swim_hours = sum(v for v in swim_history.values() if isinstance(v, (int, float))) / 3600
    except Exception:
        total_swim_hours = 0

    try:
        match_history = load_match_history(page)
        total_matches = sum(v for v in match_history.values() if isinstance(v, (int, float))) if isinstance(match_history, dict) else 0
    except Exception:
        total_matches = 0

    return {
        "focus_hours": total_focus_hours,
        "gym_sessions": total_gym_sessions,
        "swim_hours": total_swim_hours,
        "matches": total_matches,
    }


def check_new_milestones(page: ft.Page):
    """Yeni gecilmis (henuz kutlanmamis) kilometre taslarini bulur.
    Doner: [(kategori, esik), ...]"""
    try:
        stats = compute_alltime_stats(page)
        celebrated = load_celebrated_milestones(page)
    except Exception:
        return []

    newly_crossed = []
    updated = False
    for category, thresholds in MILESTONE_THRESHOLDS.items():
        current_value = stats.get(category, 0)
        for t in thresholds:
            key = f"{category}_{t}"
            if current_value >= t and key not in celebrated:
                newly_crossed.append((category, t))
                celebrated.add(key)
                updated = True

    if updated:
        try:
            save_celebrated_milestones(page, celebrated)
        except Exception:
            pass

    return newly_crossed


# --- Uzun vadeli hedef ---
def load_long_term_goal(page: ft.Page):
    data = _load_cached_or_remote(page, "longterm_goal_json", "longterm_goal")
    return data if isinstance(data, dict) and data.get("target_hours") else None


def save_long_term_goal(page: ft.Page, target_hours: float, target_date_str: str):
    data = {
        "target_hours": target_hours,
        "target_date": target_date_str,
        "start_date": datetime.now().strftime("%Y-%m-%d"),
    }
    _save_cached_and_remote(page, "longterm_goal_json", "longterm_goal", data)


def clear_long_term_goal(page: ft.Page):
    _save_cached_and_remote(page, "longterm_goal_json", "longterm_goal", {})


def compute_long_term_goal_progress(page: ft.Page):
    goal = load_long_term_goal(page)
    if not goal:
        return None
    try:
        target_hours = float(goal["target_hours"])
        target_date = datetime.strptime(goal["target_date"], "%Y-%m-%d").date()
        start_date = datetime.strptime(goal["start_date"], "%Y-%m-%d").date()
    except (ValueError, KeyError, TypeError):
        return None

    try:
        history = load_history(page)
    except Exception:
        history = {}

    current_hours = 0.0
    for d, s in history.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dt >= start_date:
            current_hours += s / 3600

    today = datetime.now().date()
    days_total = max((target_date - start_date).days, 1)
    days_elapsed = max((today - start_date).days, 1)
    days_remaining = (target_date - today).days

    required_pace_per_week = (target_hours / days_total) * 7
    actual_pace_per_week = (current_hours / days_elapsed) * 7

    pct = min(100.0, (current_hours / target_hours) * 100) if target_hours > 0 else 0

    return {
        "target_hours": target_hours,
        "current_hours": current_hours,
        "pct": pct,
        "days_remaining": days_remaining,
        "required_pace_per_week": required_pace_per_week,
        "actual_pace_per_week": actual_pace_per_week,
        "on_track": actual_pace_per_week >= required_pace_per_week * 0.9,
        "reached": current_hours >= target_hours,
    }


# --- Uzun vadeli dusus uyarisi ---
def compute_long_term_trend(page: ft.Page):
    try:
        history = load_history(page)
    except Exception:
        return None
    today = datetime.now().date()
    recent_start = today - timedelta(weeks=4)
    prior_start = today - timedelta(weeks=8)

    recent_total = 0.0
    prior_total = 0.0
    for d, s in history.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if recent_start <= dt <= today:
            recent_total += s
        elif prior_start <= dt < recent_start:
            prior_total += s

    recent_avg_week = (recent_total / 3600) / 4
    prior_avg_week = (prior_total / 3600) / 4
    if prior_avg_week <= 0.2:
        return None
    change_pct = ((recent_avg_week - prior_avg_week) / prior_avg_week) * 100
    if change_pct > -25:
        return None
    return {"recent_avg": recent_avg_week, "prior_avg": prior_avg_week, "change_pct": change_pct}


# --- Ilk kullanim tarihi (yil donumu surprizi icin) ---
def get_or_set_first_use_date(page: ft.Page):
    try:
        existing = page.client_storage.get("first_use_date")
    except Exception:
        existing = None
    if existing:
        return existing
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        page.client_storage.set("first_use_date", today_str)
    except Exception:
        pass
    return today_str


def compute_anniversary_years(first_use_date_str):
    try:
        first_date = datetime.strptime(first_use_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    today = datetime.now().date()
    if today == first_date:
        return None  # ilk gun kutlama yok
    years = today.year - first_date.year
    if years < 1:
        return None
    if (today.month, today.day) == (first_date.month, first_date.day):
        return years
    return None


# --- Gune not ekleme ---
DAY_NOTES_KEY = "day_notes"


def load_day_notes(page: ft.Page) -> dict:
    return _load_cached_or_remote(page, "day_notes_json", DAY_NOTES_KEY)


def save_day_notes(page: ft.Page, notes: dict):
    _save_cached_and_remote(page, "day_notes_json", DAY_NOTES_KEY, notes)


def set_day_note(page: ft.Page, date_str: str, text: str):
    notes = load_day_notes(page)
    if not isinstance(notes, dict):
        notes = {}
    if text.strip():
        notes[date_str] = text.strip()
    else:
        notes.pop(date_str, None)
    save_day_notes(page, notes)


def delete_day_note(page: ft.Page, date_str: str):
    notes = load_day_notes(page)
    if not isinstance(notes, dict):
        notes = {}
    notes.pop(date_str, None)
    save_day_notes(page, notes)


# --- Gunu sifirlama (tek merkezi islem) ---
def delete_focus_entry(page: ft.Page, date_str: str):
    history = load_history(page)
    if isinstance(history, dict):
        history.pop(date_str, None)
        save_history(page, history)


def delete_extra_for_date(page: ft.Page, date_str: str):
    history = load_extra_history(page)
    if isinstance(history, dict):
        history.pop(date_str, None)
        save_extra_history(page, history)


def reset_gym_entries_for_date(page: ft.Page, date_str: str):
    """O tarihte tamamlanmis gym antrenmanlarini 'yapilmadi' durumuna geri alir."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return
    week_key = week_key_for_date(dt)
    gym_history = load_gym_history(page)
    if not isinstance(gym_history, dict):
        return
    done_map = gym_history.get(week_key, {})
    if not isinstance(done_map, dict):
        return
    changed = False
    for i in range(4):
        entry = get_gym_entry(done_map, i)
        if entry["done"] and entry["date"] == date_str:
            done_map[str(i)] = {"done": False, "date": None}
            changed = True
    if changed:
        gym_history[week_key] = done_map
        save_gym_history(page, gym_history)


def reset_day_data(page: ft.Page, date_str: str):
    """Belirli bir gunun tum verilerini (odaklanma, yuzme, gym, kilo, mac,
    ekstra antrenman, not) sifirlar."""
    for func, args in [
        (delete_focus_entry, (page, date_str)),
        (set_swim_seconds_for_date, (page, date_str, 0)),
        (delete_weight_entry, (page, date_str)),
        (set_match_count_for_date, (page, date_str, 0)),
        (delete_extra_for_date, (page, date_str)),
        (reset_gym_entries_for_date, (page, date_str)),
        (delete_day_note, (page, date_str)),
    ]:
        try:
            func(*args)
        except Exception:
            pass


def save_weight_goal(page: ft.Page, direction: str):
    _save_cached_and_remote(page, "weight_goal_json", WEIGHT_GOAL_KEY, {"direction": direction})


def compute_weekly_weight_averages(history: dict, weeks_back: int = 6):
    """Son N haftanin ortalama kilosunu, kronolojik sirada hesaplar.
    Doner: [(week_key, ortalama_kg), ...] -- veri olmayan haftalar atlanir."""
    week_values = {}
    for date_str, kg in history.items():
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        wk = week_key_for_date(d)
        week_values.setdefault(wk, []).append(kg)

    sorted_weeks = sorted(week_values.keys())
    result = [(wk, sum(week_values[wk]) / len(week_values[wk])) for wk in sorted_weeks]
    return result[-weeks_back:]


def compute_weight_trend_and_prediction(history: dict):
    """Basit dogrusal regresyon ile haftalik egilimi (kg/hafta) ve bir
    sonraki haftanin tahmini ortalamasini hesaplar. Yeterli veri yoksa None doner."""
    weekly = compute_weekly_weight_averages(history, weeks_back=8)
    if len(weekly) < 2:
        return None

    n = len(weekly)
    xs = list(range(n))
    ys = [v for _, v in weekly]
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom = (n * sum_x2 - sum_x * sum_x)
    if denom == 0:
        slope = 0.0
    else:
        slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    prediction = intercept + slope * n

    current_week_avg = ys[-1]
    prev_week_avg = ys[-2] if n >= 2 else None
    week_over_week_change = (current_week_avg - prev_week_avg) if prev_week_avg is not None else None

    return {
        "slope_per_week": slope,
        "current_week_avg": current_week_avg,
        "week_over_week_change": week_over_week_change,
        "prediction_next_week": prediction,
    }


def load_gym_history(page: ft.Page) -> dict:
    return _load_cached_or_remote(page, "gym_json", GYM_KEY)


def save_gym_history(page: ft.Page, history: dict):
    _save_cached_and_remote(page, "gym_json", GYM_KEY, history)


def get_gym_entry(done_map, index: int):
    """Gym girisini normalize eder: eski format (bool) ya da yeni format
    ({'done':.., 'date':..}) her ikisini de destekler; beklenmedik veri
    turlerine karsi guvenlidir."""
    if not isinstance(done_map, dict):
        return {"done": False, "date": None}
    raw = done_map.get(str(index))
    if raw is None:
        return {"done": False, "date": None}
    if isinstance(raw, dict):
        return {"done": bool(raw.get("done", False)), "date": raw.get("date")}
    return {"done": bool(raw), "date": None}


def mark_gym_done(page: ft.Page, week_key: str, index: int, date_str: str):
    history = load_gym_history(page)
    done_map = history.get(week_key, {})
    done_map[str(index)] = {"done": True, "date": date_str}
    history[week_key] = done_map
    save_gym_history(page, history)


EXTRA_KEY = "gym_extra_history"


def load_extra_history(page: ft.Page) -> dict:
    return _load_cached_or_remote(page, "extra_json", EXTRA_KEY)


def save_extra_history(page: ft.Page, history: dict):
    _save_cached_and_remote(page, "extra_json", EXTRA_KEY, history)


def sum_extra_for_day(entry):
    """Eski format (int) ve yeni format ({tur: sayi}) icin toplam sayiyi dondurur."""
    if entry is None:
        return 0
    if isinstance(entry, dict):
        return sum(entry.values())
    return entry


def add_extra_workout(page: ft.Page, date_str: str, workout_type: str):
    history = load_extra_history(page)
    day_entry = history.get(date_str)
    if not isinstance(day_entry, dict):
        day_entry = {}
    day_entry[workout_type] = day_entry.get(workout_type, 0) + 1
    history[date_str] = day_entry
    save_extra_history(page, history)


GYM_WORKOUTS_TR = ["Bacak + Omuz", "Göğüs + Triceps", "Sırt + Arka Kol", "Bacak + Core"]
GYM_WORKOUTS_EN = ["Legs + Shoulders", "Chest + Triceps", "Back + Biceps", "Legs + Core"]
GYM_WORKOUTS = GYM_WORKOUTS_TR  # geriye donuk uyumluluk icin varsayilan (TR)

EXTRA_ONLY_TR = ["Core", "Kardiyo"]
EXTRA_ONLY_EN = ["Core", "Cardio"]
EXTRA_WORKOUT_TYPES = GYM_WORKOUTS_TR + EXTRA_ONLY_TR

TURKISH_WEEKDAY_NAMES = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
ENGLISH_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def gym_workouts_for(lang):
    return GYM_WORKOUTS_EN if lang == "en" else GYM_WORKOUTS_TR


def extra_types_for(lang):
    base = GYM_WORKOUTS_EN if lang == "en" else GYM_WORKOUTS_TR
    extra = EXTRA_ONLY_EN if lang == "en" else EXTRA_ONLY_TR
    return base + extra


def weekday_names_for(lang):
    return ENGLISH_WEEKDAY_NAMES if lang == "en" else TURKISH_WEEKDAY_NAMES


def workout_name_to_index(name, lang):
    """Herhangi bir dildeki antrenman ismini sabit index'e (0-3) cevirir,
    boylece veriler dil degistiginde de tutarli kalir."""
    lists = [GYM_WORKOUTS_TR, GYM_WORKOUTS_EN]
    for lst in lists:
        if name in lst:
            return lst.index(name)
    return None


def current_week_key():
    iso = datetime.now().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def week_key_for_date(d: datetime):
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


WEEKDAY_LABELS_TR = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]
WEEKDAY_LABELS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_LABELS = WEEKDAY_LABELS_TR  # geriye donuk uyumluluk

MONTH_LABELS_TR = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]
MONTH_LABELS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_LABELS = MONTH_LABELS_TR  # geriye donuk uyumluluk


def weekday_labels_for(lang):
    return WEEKDAY_LABELS_EN if lang == "en" else WEEKDAY_LABELS_TR


def month_labels_for(lang):
    return MONTH_LABELS_EN if lang == "en" else MONTH_LABELS_TR


# ---------------------------------------------------------
# CEVIRI (TR/EN) SISTEMI
# ---------------------------------------------------------
TRANSLATIONS = {
    "app_title": {"tr": "Kişisel Organizer", "en": "Personal Organizer"},
    "loading": {"tr": "Hazırlanıyor...", "en": "Preparing..."},
    "change_theme": {"tr": "Tema değiştir", "en": "Change theme"},
    "account_badge_guest": {"tr": "Hesap", "en": "Account"},
    "account_badge_code": {"tr": "Kod: {code}", "en": "Code: {code}"},

    "tab_focus": {"tr": "Odaklanma", "en": "Focus"},
    "tab_calendar": {"tr": "Takvim", "en": "Calendar"},
    "tab_performance": {"tr": "Performans", "en": "Performance"},
    "tab_sports": {"tr": "Spor", "en": "Sports"},

    # --- Odaklanma ---
    "minutes_label": {"tr": "Dakika", "en": "Minutes"},
    "focus_duration_suffix": {"tr": "dakika odaklanma süresi", "en": "minutes of focus time"},
    "start": {"tr": "Başlat", "en": "Start"},
    "stop": {"tr": "Durdur", "en": "Stop"},
    "reset": {"tr": "Sıfırla", "en": "Reset"},
    "focus_started": {"tr": "Odaklanma başladı", "en": "Focus started"},
    "stopped": {"tr": "Durduruldu", "en": "Stopped"},
    "enter_valid_minutes": {"tr": "Lütfen geçerli bir dakika gir.", "en": "Please enter a valid number of minutes."},
    "time_up_overtime": {
        "tr": "Süre doldu! Fazladan geçen süre sayılıyor.",
        "en": "Time's up! Counting extra time.",
    },
    "greeting_morning": {"tr": "Günaydın", "en": "Good morning"},
    "greeting_afternoon": {"tr": "İyi günler", "en": "Good afternoon"},
    "greeting_evening": {"tr": "İyi akşamlar", "en": "Good evening"},
    "today_stat": {"tr": "Bugün: {v}", "en": "Today: {v}"},
    "streak_stat": {"tr": "Seri: {v} gün", "en": "Streak: {v} days"},

    # --- Takvim ---
    "week_col": {"tr": "Hafta", "en": "Week"},
    "month_total": {"tr": "Bu ay toplam: {v}", "en": "This month's total: {v}"},

    # --- Performans ---
    "weekly_total": {"tr": "Haftalık toplam: {v} saat", "en": "Weekly total: {v} hours"},
    "reports_title": {"tr": "Raporlar", "en": "Reports"},
    "this_week": {"tr": "Bu Hafta", "en": "This Week"},
    "this_month": {"tr": "Bu Ay", "en": "This Month"},
    "completed_prefix": {"tr": "Tamamlanan: {v}", "en": "Completed: {v}"},
    "missing_prefix": {"tr": "Eksik: {v}", "en": "Missing: {v}"},
    "no_week_data": {"tr": "Bu hafta için veri yok.", "en": "No data for this week."},
    "extra_count": {"tr": "Ekstra antrenman: {v}", "en": "Extra workouts: {v}"},
    "gym_sessions_report": {
        "tr": "Bu ay {possible} gym seansından {completed} tanesini gerçekleştirdiniz.",
        "en": "You completed {completed} out of {possible} gym sessions this month.",
    },
    "no_gym_data_month": {"tr": "Bu ay henüz gym verisi yok.", "en": "No gym data yet this month."},
    "swim_month_report": {"tr": "Bu ay {h:.1f} saat yüzdünüz.", "en": "You swam {h:.1f} hours this month."},
    "weakest_workout_report": {
        "tr": "Bu ay en az yaptığınız antrenman: {name} ({count}/{possible} hafta).",
        "en": "Your least completed workout this month: {name} ({count}/{possible} weeks).",
    },
    "weekday_pattern_report": {
        "tr": "{weekday} günleri en çok {workout} yaptınız ({count} kez).",
        "en": "You mostly do {workout} on {weekday}s ({count} times).",
    },

    # --- Profesyonel analiz: kas grubu takibi ---
    "muscle_tracking_title": {"tr": "Antrenman Tazeliği", "en": "Training Freshness"},
    "never_done": {"tr": "Hiç yapılmadı", "en": "Never done"},
    "done_today": {"tr": "Bugün yapıldı", "en": "Done today"},
    "days_ago": {"tr": "{n} gün önce", "en": "{n} days ago"},
    "overdue_tag": {"tr": " (gecikmiş)", "en": " (overdue)"},

    # --- Profesyonel analiz: sureklilik skoru ---
    "consistency_title": {"tr": "Süreklilik", "en": "Consistency"},
    "consistency_compare": {
        "tr": "Bu ay %{now:.0f} tamamlama (geçen ay %{prev:.0f}, {delta})",
        "en": "{now:.0f}% completion this month (last month {prev:.0f}%, {delta})",
    },
    "consistency_no_prev": {
        "tr": "Bu ay %{now:.0f} tamamlama (geçen ay veri yok)",
        "en": "{now:.0f}% completion this month (no data last month)",
    },
    "delta_up": {"tr": "+{v:.0f} puan", "en": "+{v:.0f} points"},
    "delta_down": {"tr": "{v:.0f} puan", "en": "{v:.0f} points"},
    "delta_same": {"tr": "değişim yok", "en": "no change"},

    # --- Profesyonel analiz: uyarilar ---
    "warnings_title": {"tr": "Uyarılar", "en": "Warnings"},
    "overtraining_warning": {
        "tr": "{n} gündür ara vermeden aktifsiniz. Bir dinlenme günü düşünün.",
        "en": "You've been active for {n} days straight. Consider a rest day.",
    },
    "inactivity_nudge": {
        "tr": "{n} gündür herhangi bir aktivite yok. Tekrar başlamaya ne dersin?",
        "en": "No activity for {n} days. How about getting back to it?",
    },
    "balanced_pace": {"tr": "Dengeli bir tempoda gidiyorsunuz.", "en": "You're keeping a balanced pace."},

    # --- Profesyonel analiz: gunluk oneri ---
    "recommendation_title": {"tr": "Bugünün Önerisi", "en": "Today's Recommendation"},
    "rec_rest": {
        "tr": "Son günlerde yoğundunuz -- bugün dinlenme günü iyi olabilir.",
        "en": "You've been busy lately -- today might be a good rest day.",
    },
    "rec_workout": {
        "tr": "Bugün için öneri: {workout} antrenmanı.",
        "en": "Suggestion for today: {workout} workout.",
    },
    "rec_swim": {
        "tr": "Bu hafta henüz yüzmediniz -- bugün havuza ne dersiniz?",
        "en": "You haven't swum this week yet -- how about the pool today?",
    },
    "rec_all_done": {
        "tr": "Bu haftaki hedeflerinizi tamamladınız! Hafif bir aktivite veya dinlenme iyi olur.",
        "en": "You've completed this week's goals! Light activity or rest would be good.",
    },

    # --- Profesyonel analiz: kisisel rekorlar ---
    "records_title": {"tr": "Kişisel Rekorlar", "en": "Personal Records"},
    "record_longest_streak": {"tr": "En uzun odaklanma serisi: {n} gün", "en": "Longest focus streak: {n} days"},
    "record_best_focus_week": {"tr": "En yoğun odaklanma haftası: {h:.1f} saat", "en": "Best focus week: {h:.1f} hours"},
    "record_best_gym_week": {"tr": "En çok gym tamamlanan hafta: {n}/4", "en": "Best gym week: {n}/4"},
    "no_records_yet": {"tr": "Henüz yeterli veri yok.", "en": "Not enough data yet."},

    # --- Kilo takibi ---
    "sub_weight": {"tr": "Kilo", "en": "Weight"},
    "weight_field_label": {"tr": "Kilo (kg)", "en": "Weight (kg)"},
    "weight_save_btn": {"tr": "Bugünkü Kilonu Kaydet", "en": "Save Today's Weight"},
    "weight_saved": {"tr": "Kaydedildi!", "en": "Saved!"},
    "weight_enter_valid": {"tr": "Lütfen geçerli bir kilo gir (20-300 kg).", "en": "Please enter a valid weight (20-300 kg)."},
    "weight_goal_gain": {"tr": "Kilo Almaya Çalışıyorum", "en": "I'm Trying to Gain"},
    "weight_goal_lose": {"tr": "Kilo Vermeye Çalışıyorum", "en": "I'm Trying to Lose"},
    "weight_goal_maintain": {"tr": "Korumaya Çalışıyorum", "en": "I'm Trying to Maintain"},
    "weight_chart_title": {"tr": "Kilo Grafiği", "en": "Weight Chart"},
    "weight_weekly_avg": {"tr": "Bu haftaki ortalama: {v:.1f} kg", "en": "This week's average: {v:.1f} kg"},
    "weight_vs_last_week": {"tr": "Geçen haftaya göre: {v}", "en": "vs last week: {v}"},
    "weight_change_up": {"tr": "+{v:.1f} kg", "en": "+{v:.1f} kg"},
    "weight_change_down": {"tr": "{v:.1f} kg", "en": "{v:.1f} kg"},
    "weight_change_same": {"tr": "değişim yok", "en": "no change"},
    "weight_prediction": {
        "tr": "Mevcut eğiliminizle önümüzdeki hafta tahmini: {v:.1f} kg",
        "en": "At your current trend, next week's estimate: {v:.1f} kg",
    },
    "weight_prediction_disclaimer": {
        "tr": "Bu sadece güncel eğiliminize dayanan matematiksel bir tahmindir, tıbbi tavsiye değildir.",
        "en": "This is only a mathematical estimate based on your current trend, not medical advice.",
    },
    "weight_not_enough_data": {
        "tr": "Tahmin için en az 2 haftalık veri gerekiyor.",
        "en": "At least 2 weeks of data are needed for a prediction.",
    },
    "weight_on_track_gain": {"tr": "Eğiliminiz kilo alma hedefinizle uyumlu.", "en": "Your trend matches your weight-gain goal."},
    "weight_on_track_lose": {"tr": "Eğiliminiz kilo verme hedefinizle uyumlu.", "en": "Your trend matches your weight-loss goal."},
    "weight_off_track_gain": {
        "tr": "Kilo almayı hedefliyorsunuz ama eğiliminiz artmıyor gibi görünüyor.",
        "en": "You're aiming to gain, but your trend doesn't seem to be increasing.",
    },
    "weight_off_track_lose": {
        "tr": "Kilo vermeyi hedefliyorsunuz ama eğiliminiz azalmıyor gibi görünüyor.",
        "en": "You're aiming to lose, but your trend doesn't seem to be decreasing.",
    },

    # --- Spor / Mac ---
    "sub_match": {"tr": "Maç", "en": "Match"},
    "match_add_btn": {"tr": "Maç Oynadım", "en": "Played a Match"},
    "match_added": {"tr": "Maç eklendi!", "en": "Match added!"},
    "match_today": {"tr": "Bugün: {v}", "en": "Today: {v}"},
    "match_this_week": {"tr": "Bu hafta: {v}", "en": "This week: {v}"},
    "match_this_month": {"tr": "Bu ay: {v}", "en": "This month: {v}"},
    "match_total": {"tr": "Toplam: {v}", "en": "Total: {v}"},

    # --- Haftalik ozet raporu (sadece Pazar 22:00-23:59) ---
    "weekly_recap_title": {"tr": "Haftalık Özet", "en": "Weekly Recap"},
    "weekly_recap_subtitle": {
        "tr": "Bu özet sadece pazar gecesi (22:00-23:59) görünür.",
        "en": "This summary is only visible on Sunday nights (10:00-11:59 PM).",
    },
    "recap_focus_total": {"tr": "Odaklanma: {v:.1f} saat", "en": "Focus: {v:.1f} hours"},
    "recap_focus_vs_avg_above": {
        "tr": "Bu hafta odaklanma ortalamanızın üzerinde ({v:.1f} saat / hafta ortalaması).",
        "en": "Focus this week is above your average ({v:.1f} hours/week average).",
    },
    "recap_focus_vs_avg_below": {
        "tr": "Bu hafta odaklanma ortalamanızın altında ({v:.1f} saat / hafta ortalaması).",
        "en": "Focus this week is below your average ({v:.1f} hours/week average).",
    },
    "recap_swim_total": {"tr": "Yüzme: {v:.1f} saat", "en": "Swim: {v:.1f} hours"},
    "recap_gym_total": {"tr": "Gym: {completed}/{possible} tamamlandı", "en": "Gym: {completed}/{possible} completed"},
    "recap_gym_missing": {"tr": "Eksik antrenman: {v}", "en": "Missing workout: {v}"},
    "recap_extra_total": {"tr": "Ekstra antrenman: {v}", "en": "Extra workouts: {v}"},
    "recap_match_total": {"tr": "Maç: {v}", "en": "Matches: {v}"},
    "recap_weight_change": {"tr": "Kilo değişimi: {v}", "en": "Weight change: {v}"},
    "recap_active_streak": {"tr": "Aktif seri: {v} gün", "en": "Active streak: {v} days"},
    "recap_best_day": {"tr": "En yoğun gününüz: {v}", "en": "Your busiest day: {v}"},

    # --- Korelasyon analizi ---
    "correlation_title": {"tr": "Korelasyon Analizi", "en": "Correlation Analysis"},
    "correlation_gym": {
        "tr": "Gym yaptığınız günlerde ortalama odaklanma: {gym_avg:.1f} saat (yapmadığınız günlerde: {non_avg:.1f} saat).",
        "en": "Average focus on gym days: {gym_avg:.1f} hours (on non-gym days: {non_avg:.1f} hours).",
    },
    "correlation_swim": {
        "tr": "Yüzdüğünüz günlerde ortalama odaklanma: {swim_avg:.1f} saat (yüzmediğiniz günlerde: {non_avg:.1f} saat).",
        "en": "Average focus on swim days: {swim_avg:.1f} hours (on non-swim days: {non_avg:.1f} hours).",
    },
    "correlation_no_data": {"tr": "Anlamlı bir korelasyon için henüz yeterli veri yok.", "en": "Not enough data yet for a meaningful correlation."},
    "correlation_disclaimer": {
        "tr": "Bu sadece bir gözlemdir, nedensellik anlamına gelmez.",
        "en": "This is only an observation, not a claim of causation.",
    },

    # --- Yillik genel bakis ---
    "yearly_title": {"tr": "Bu Yıl", "en": "This Year"},
    "yearly_focus": {"tr": "Toplam odaklanma: {v:.1f} saat", "en": "Total focus: {v:.1f} hours"},
    "yearly_swim": {"tr": "Toplam yüzme: {v:.1f} saat", "en": "Total swim: {v:.1f} hours"},
    "yearly_gym": {"tr": "Gym: {completed}/{possible} tamamlandı", "en": "Gym: {completed}/{possible} completed"},
    "yearly_best_month": {"tr": "En yoğun ayınız: {name} ({h:.1f} saat)", "en": "Your busiest month: {name} ({h:.1f} hours)"},
    "yearly_matches": {"tr": "Toplam maç: {v}", "en": "Total matches: {v}"},
    "yearly_no_data": {"tr": "Bu yıl için henüz veri yok.", "en": "No data for this year yet."},

    # --- Aliskanlik takibi ---
    "tab_habits": {"tr": "Alışkanlıklar", "en": "Habits"},
    "habit_name_label": {"tr": "Yeni alışkanlık adı", "en": "New habit name"},
    "add_habit_btn": {"tr": "Ekle", "en": "Add"},
    "no_habits_yet": {
        "tr": "Henüz alışkanlık eklemediniz. Yukarıdan bir tane ekleyin.",
        "en": "You haven't added any habits yet. Add one above.",
    },
    "habits_today_title": {"tr": "Bugün", "en": "Today"},

    # --- Gorsel iyilestirmeler: legend, bos durumlar ---
    "calendar_legend_low": {"tr": "<1s", "en": "<1h"},
    "calendar_legend_mid": {"tr": "1-3s", "en": "1-3h"},
    "calendar_legend_high": {"tr": "3-6s", "en": "3-6h"},
    "calendar_legend_max": {"tr": "6s+", "en": "6h+"},

    # --- Kilometre taslari ---
    "milestone_title": {"tr": "Tebrikler!", "en": "Congratulations!"},
    "milestone_focus": {"tr": "Toplam {n} saat odaklandınız!", "en": "You've focused a total of {n} hours!"},
    "milestone_gym": {"tr": "Toplam {n} gym seansı tamamladınız!", "en": "You've completed a total of {n} gym sessions!"},
    "milestone_swim": {"tr": "Toplam {n} saat yüzdünüz!", "en": "You've swum a total of {n} hours!"},
    "milestone_match": {"tr": "Toplam {n} maç oynadınız!", "en": "You've played a total of {n} matches!"},
    "milestone_close_btn": {"tr": "Harika!", "en": "Awesome!"},

    # --- Uzun vadeli hedef ---
    "longterm_goal_title": {"tr": "Uzun Vadeli Hedef", "en": "Long-Term Goal"},
    "longterm_goal_intro": {
        "tr": "Belirli bir tarihe kadar toplam kaç saat odaklanmak istediğinizi belirleyin.",
        "en": "Set how many total hours you want to focus by a specific date.",
    },
    "longterm_hours_label": {"tr": "Hedef saat", "en": "Target hours"},
    "longterm_date_label": {"tr": "Hedef tarih (YYYY-AA-GG)", "en": "Target date (YYYY-MM-DD)"},
    "longterm_set_btn": {"tr": "Hedef Belirle", "en": "Set Goal"},
    "longterm_clear_btn": {"tr": "Hedefi Sil", "en": "Clear Goal"},
    "longterm_invalid": {"tr": "Lütfen geçerli bir saat ve tarih gir.", "en": "Please enter a valid hour and date."},
    "longterm_progress": {"tr": "{current:.1f} / {target:.0f} saat (%{pct:.0f})", "en": "{current:.1f} / {target:.0f} hours ({pct:.0f}%)"},
    "longterm_days_left": {"tr": "{n} gün kaldı", "en": "{n} days left"},
    "longterm_days_over": {"tr": "Hedef tarihi geçti", "en": "Target date has passed"},
    "longterm_pace_ontrack": {
        "tr": "Temponuz hedefe uygun (gerekli: {req:.1f} saat/hafta, siz: {actual:.1f} saat/hafta).",
        "en": "Your pace matches the goal (needed: {req:.1f} h/week, you: {actual:.1f} h/week).",
    },
    "longterm_pace_behind": {
        "tr": "Hedefin gerisindesiniz (gerekli: {req:.1f} saat/hafta, siz: {actual:.1f} saat/hafta).",
        "en": "You're behind the goal (needed: {req:.1f} h/week, you: {actual:.1f} h/week).",
    },
    "longterm_reached": {"tr": "Hedefinize ulaştınız! Tebrikler!", "en": "You've reached your goal! Congratulations!"},

    # --- Uzun vadeli dusus uyarisi ---
    "longterm_decline_warning": {
        "tr": "Son 2 aydır genel bir düşüş var: haftalık ortalama {prior:.1f} saatten {recent:.1f} saate geriledi.",
        "en": "There's been an overall decline over the last 2 months: weekly average dropped from {prior:.1f} to {recent:.1f} hours.",
    },

    # --- Gece karsilamasi (surpriz) ---
    "greeting_night": {"tr": "İyi geceler", "en": "Good night"},
    "anniversary_banner": {
        "tr": "Bu uygulamayı {n}. yıldır kullanıyorsunuz! İyi ki varsınız.",
        "en": "You've been using this app for {n} year(s)! Great to have you here.",
    },

    # --- Veri duzeltme (hatali giris) ---
    "edit_entry_title": {"tr": "Kaydı Düzelt", "en": "Edit Entry"},
    "edit_date_label": {"tr": "Tarih (YYYY-AA-GG)", "en": "Date (YYYY-MM-DD)"},
    "edit_value_label": {"tr": "Doğru değer", "en": "Correct value"},
    "edit_save_btn": {"tr": "Kaydet", "en": "Save"},
    "edit_delete_btn": {"tr": "Bu Günü Sil", "en": "Delete This Day"},
    "edit_invalid_date": {"tr": "Lütfen geçerli bir tarih gir (YYYY-AA-GG).", "en": "Please enter a valid date (YYYY-MM-DD)."},
    "edit_saved": {"tr": "Düzeltildi!", "en": "Corrected!"},
    "edit_deleted": {"tr": "Kayıt silindi.", "en": "Entry deleted."},

    # --- Aliskanlik seri/gecmis ---
    "habit_streak": {"tr": "{n} günlük seri", "en": "{n}-day streak"},
    "habit_last_30": {"tr": "Son 30 günde {n} kez", "en": "{n} times in last 30 days"},

    # --- Gune not ekleme ---
    "day_note_title": {"tr": "Gün Notu", "en": "Day Note"},
    "day_note_label": {"tr": "Bu gün için not", "en": "Note for this day"},
    "day_note_save_btn": {"tr": "Kaydet", "en": "Save"},
    "day_note_delete_btn": {"tr": "Notu Sil", "en": "Delete Note"},

    # --- Veri yedekleme ---
    "backup_title": {"tr": "Veri Yedekleme", "en": "Data Backup"},
    "backup_intro": {
        "tr": "Tüm verilerini bir metin olarak panoya kopyalayıp güvenli bir yere yapıştırarak saklayabilirsin.",
        "en": "You can copy all your data as text to your clipboard and save it somewhere safe.",
    },
    "backup_copy_btn": {"tr": "Verilerimi Kopyala", "en": "Copy My Data"},
    "backup_copied": {"tr": "Kopyalandı! Bir not uygulamasına yapıştırıp saklayabilirsin.", "en": "Copied! You can paste it into a notes app to save it."},

    # --- Gunu sifirlama (tek merkezi buton) ---
    "reset_day_title": {"tr": "Günü Sıfırla", "en": "Reset Day"},
    "reset_day_intro": {
        "tr": "Belirli bir günün TÜM verilerini (odaklanma, spor, kilo, notlar, alışkanlıklar) sıfırlar.",
        "en": "Resets ALL data (focus, sports, weight, notes, habits) for a specific day.",
    },
    "reset_day_btn": {"tr": "Günü Sıfırla", "en": "Reset Day"},
    "reset_day_confirm_title": {"tr": "Emin misiniz?", "en": "Are you sure?"},
    "reset_day_confirm_body": {
        "tr": "{date} tarihli TÜM veriler silinecek. Bu işlem geri alınamaz.",
        "en": "ALL data for {date} will be deleted. This cannot be undone.",
    },
    "reset_day_done": {"tr": "Gün sıfırlandı.", "en": "Day reset."},

    # --- Gorunum ayarlari (renk paleti secimi) ---
    "appearance_title": {"tr": "Görünüm", "en": "Appearance"},
    "heat_palette_label": {"tr": "Takvim renk geçişi", "en": "Calendar color scheme"},
    "mode_new_account": {"tr": "Yeni Hesap", "en": "New Account"},
    "mode_join_account": {"tr": "Kodum Var", "en": "I Have a Code"},

    # --- Spor / Yuzme ---
    "sub_swim": {"tr": "Yüzme", "en": "Swim"},
    "sub_gym": {"tr": "Gym", "en": "Gym"},
    "swim_duration_suffix": {"tr": "dakika yüzüldü", "en": "minutes swum"},
    "save": {"tr": "Kaydet", "en": "Save"},
    "swum_today": {"tr": "Bugün yüzülen: {v}", "en": "Swum today: {v}"},
    "enter_valid_duration": {"tr": "Lütfen geçerli bir süre gir.", "en": "Please enter a valid duration."},
    "unrealistic_duration": {
        "tr": "Lütfen gerçekçi bir süre gir (tek seferde en fazla 300 dakika / 5 saat).",
        "en": "Please enter a realistic duration (max 300 minutes / 5 hours per entry).",
    },
    "over_24h": {
        "tr": "Bu, bugünkü toplamı 24 saatin üzerine çıkarır. Lütfen kontrol et.",
        "en": "This would push today's total over 24 hours. Please check.",
    },
    "saved": {"tr": "Kaydedildi!", "en": "Saved!"},
    "month_total_swim": {"tr": "Ay toplamı: {v:.1f} saat", "en": "Month total: {v:.1f} hours"},

    # --- Spor / Gym ---
    "week_program": {"tr": "Bu haftanın antrenman programı", "en": "This week's workout schedule"},
    "add_extra_workout": {"tr": "Ekstra Antrenman Ekle", "en": "Add Extra Workout"},
    "confirm_workout_title": {"tr": "Antrenmanı Onayla", "en": "Confirm Workout"},
    "confirm_workout_body": {
        "tr": "'{name}' antrenmanını tamamladınız mı? Onayladıktan sonra bu hafta için geri alınamaz.",
        "en": "Did you complete '{name}'? Once confirmed, it cannot be undone this week.",
    },
    "cancel": {"tr": "Vazgeç", "en": "Cancel"},
    "yes_completed": {"tr": "Evet, Tamamlandı", "en": "Yes, Completed"},
    "extra_workout_type_title": {"tr": "Ekstra Antrenman Türü", "en": "Extra Workout Type"},
    "extra_added": {"tr": "Ekstra antrenman eklendi: {v}", "en": "Extra workout added: {v}"},

    # --- Hesap ---
    "account_title": {"tr": "Hesap", "en": "Account"},
    "account_intro": {
        "tr": "Sadece 1 hesap yeter: bir cihazda oluştur, diğerlerinde aynı kodla giriş yap.",
        "en": "You only need 1 account: create it on one device, log in with the same code elsewhere.",
    },
    "create_account": {"tr": "Yeni Hesap Oluştur", "en": "Create New Account"},
    "code_field_label": {"tr": "6 haneli kod", "en": "6-digit code"},
    "login_with_code": {"tr": "Kod ile Giriş Yap", "en": "Log In With Code"},
    "your_account_code": {"tr": "Hesap Kodun:", "en": "Your Account Code:"},
    "sync_hint": {
        "tr": "Bu kodu diğer cihazına (telefon/tablet) girerek verilerini senkronize edebilirsin.",
        "en": "Enter this code on your other device (phone/tablet) to sync your data.",
    },
    "change_account": {"tr": "Hesabı Değiştir / Çık", "en": "Change Account / Log Out"},
    "creating_account": {"tr": "Hesap oluşturuluyor...", "en": "Creating account..."},
    "cloud_unreachable": {"tr": "Buluta bağlanılamadı. İnternetini kontrol et.", "en": "Could not reach the cloud. Check your internet."},
    "code_generation_failed": {"tr": "Kod üretilemedi, tekrar dene.", "en": "Could not generate a code, try again."},
    "account_creation_failed": {"tr": "Hesap oluşturulamadı. İnternetini kontrol et.", "en": "Could not create account. Check your internet."},
    "account_created": {"tr": "Hesap oluşturuldu!", "en": "Account created!"},
    "enter_valid_code": {"tr": "Lütfen 6 haneli geçerli bir kod gir.", "en": "Please enter a valid 6-digit code."},
    "requests_missing": {"tr": "İnternet kütüphanesi (requests) bulunamadı.", "en": "Internet library (requests) not found."},
    "checking": {"tr": "Kontrol ediliyor...", "en": "Checking..."},
    "code_not_found": {"tr": "Bu kod bulunamadı.", "en": "This code was not found."},
    "login_success": {"tr": "Giriş başarılı!", "en": "Login successful!"},
    "logged_out": {"tr": "Hesaptan çıkıldı.", "en": "Logged out."},
    "close": {"tr": "Kapat", "en": "Close"},
}


def tr(lang, key, **kwargs):
    entry = TRANSLATIONS.get(key)
    if not entry:
        return key
    text = entry.get(lang, entry.get("tr", key))
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def get_greeting_key():
    hour = datetime.now().hour
    if 0 <= hour < 5:
        return "greeting_night"
    elif hour < 12:
        return "greeting_morning"
    elif hour < 18:
        return "greeting_afternoon"
    return "greeting_evening"


def format_hours(total_seconds, lang="tr"):
    if total_seconds <= 0:
        return ""
    hour_suffix = "h" if lang == "en" else "s"
    min_suffix = "min" if lang == "en" else "dk"
    hours = total_seconds / 3600
    if hours >= 1:
        return f"{hours:.1f}{hour_suffix}"
    minutes = total_seconds / 60
    return f"{minutes:.0f}{min_suffix}"


# Isi haritasi renk gecis noktalari (saat -> RGB). Birden fazla palet secenegi.
HEAT_PALETTES = {
    "klasik": [
        (0.5, (140, 20, 20)), (1.0, (198, 40, 40)), (1.5, (216, 67, 21)),
        (2.0, (239, 108, 0)), (2.5, (251, 140, 0)), (3.0, (100, 181, 246)),
        (3.5, (66, 153, 225)), (4.0, (33, 150, 243)), (4.5, (30, 136, 229)),
        (5.0, (25, 118, 210)), (5.5, (21, 101, 192)), (6.0, (13, 71, 161)),
        (6.5, (8, 47, 107)),
    ],
    "yesil": [
        (0.5, (74, 46, 20)), (1.0, (110, 74, 20)), (1.5, (140, 110, 20)),
        (2.0, (163, 148, 24)), (2.5, (163, 177, 40)), (3.0, (120, 176, 74)),
        (3.5, (90, 165, 84)), (4.0, (60, 150, 90)), (4.5, (40, 135, 95)),
        (5.0, (26, 117, 96)), (5.5, (20, 97, 92)), (6.0, (15, 77, 82)),
        (6.5, (10, 58, 68)),
    ],
    "mor": [
        (0.5, (74, 20, 46)), (1.0, (110, 24, 74)), (1.5, (140, 30, 110)),
        (2.0, (163, 44, 140)), (2.5, (177, 70, 163)), (3.0, (159, 100, 214)),
        (3.5, (140, 110, 224)), (4.0, (120, 110, 230)), (4.5, (100, 105, 224)),
        (5.0, (80, 95, 214)), (5.5, (65, 80, 197)), (6.0, (50, 62, 173)),
        (6.5, (36, 44, 138)),
    ],
    "tek_renk": [
        (0.5, (60, 60, 65)), (1.0, (75, 75, 82)), (1.5, (90, 90, 100)),
        (2.0, (105, 105, 118)), (2.5, (120, 120, 136)), (3.0, (137, 96, 122)),
        (3.5, (155, 88, 116)), (4.0, (172, 80, 110)), (4.5, (179, 70, 105)),
        (5.0, (186, 60, 100)), (5.5, (193, 50, 94)), (6.0, (200, 40, 88)),
        (6.5, (210, 30, 80)),
    ],
}
HEAT_PALETTE_NAMES = {
    "klasik": {"tr": "Klasik (kırmızı-mavi)", "en": "Classic (red-blue)"},
    "yesil": {"tr": "Toprak-Yeşil", "en": "Earth-Green"},
    "mor": {"tr": "Mor-Lacivert", "en": "Purple-Indigo"},
    "tek_renk": {"tr": "Gri-Vurgu", "en": "Grey-Accent"},
}
_HEAT_ANCHORS = HEAT_PALETTES["klasik"]


def heat_color_for_seconds(total_seconds, palette="klasik"):
    """Calisilan sureye gore hucre rengini dondurur (yarim saatlik yumusak
    gecislerle). 0 veya negatifse None doner (renklendirme yok)."""
    if total_seconds <= 0:
        return None
    anchors = HEAT_PALETTES.get(palette, HEAT_PALETTES["klasik"])
    hours = total_seconds / 3600
    # Yarim saatlik periyotlarda "softlasma": degeri en yakin 0.5'e yuvarla
    q = round(hours * 2) / 2
    q = max(0.5, q)
    if q >= anchors[-1][0]:
        r, g, b = anchors[-1][1]
        return f"#{r:02X}{g:02X}{b:02X}"
    for i in range(len(anchors) - 1):
        h0, c0 = anchors[i]
        h1, c1 = anchors[i + 1]
        if h0 <= q <= h1:
            t = 0 if h1 == h0 else (q - h0) / (h1 - h0)
            r = round(c0[0] + (c1[0] - c0[0]) * t)
            g = round(c0[1] + (c1[1] - c0[1]) * t)
            b = round(c0[2] + (c1[2] - c0[2]) * t)
            return f"#{r:02X}{g:02X}{b:02X}"
    r, g, b = anchors[0][1]
    return f"#{r:02X}{g:02X}{b:02X}"


def load_heat_palette(page: ft.Page) -> str:
    try:
        val = page.client_storage.get("heat_palette")
    except Exception:
        val = None
    return val if val in HEAT_PALETTES else "klasik"


def save_heat_palette(page: ft.Page, palette: str):
    try:
        page.client_storage.set("heat_palette", palette)
    except Exception:
        pass


def compute_last_done_per_workout(page: ft.Page, workout_count=4):
    """Her antrenman turu icin en son tamamlanma tarihini bulur.
    Doner: [(gun_farki_veya_None), ...] -- index sirasi GYM_WORKOUTS ile ayni."""
    gym_history = load_gym_history(page)
    last_dates = [None] * workout_count
    if isinstance(gym_history, dict):
        for week_key, done_map in gym_history.items():
            if not isinstance(done_map, dict):
                continue
            for i in range(workout_count):
                entry = get_gym_entry(done_map, i)
                if entry["done"] and entry["date"]:
                    try:
                        d = datetime.strptime(entry["date"], "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    if last_dates[i] is None or d > last_dates[i]:
                        last_dates[i] = d
    today = datetime.now().date()
    return [((today - d).days if d else None) for d in last_dates]


def compute_weekly_gym_report(page: ft.Page, week_start, lang="tr"):
    """Belirli bir haftanin (week_start = o haftanin Pazartesisi) gym durumunu hesaplar."""
    gym_history = load_gym_history(page)
    week_key = week_key_for_date(datetime(week_start.year, week_start.month, week_start.day))
    done_map = gym_history.get(week_key, {})
    names = gym_workouts_for(lang)

    completed_names = []
    missing_names = []
    for i, name in enumerate(names):
        entry = get_gym_entry(done_map, i)
        if entry["done"]:
            completed_names.append(name)
        else:
            missing_names.append(name)

    extra_history = load_extra_history(page)
    days = [week_start + timedelta(days=i) for i in range(7)]
    extra_count = sum(sum_extra_for_day(extra_history.get(d.strftime("%Y-%m-%d"))) for d in days)

    return {
        "completed_names": completed_names,
        "missing_names": missing_names,
        "extra_count": extra_count,
    }


def compute_monthly_sports_report(page: ft.Page, lang="tr", year=None, month=None):
    """Belirtilen ayin (varsayilan: bu ay) gym/yuzme istatistiklerini, en zayif
    antrenman turunu ve haftanin gunune gore en sik yapilan antrenmani hesaplar."""
    today = datetime.now()
    if year is None:
        year = today.year
    if month is None:
        month = today.month
    names = gym_workouts_for(lang)
    weekday_names = weekday_names_for(lang)

    # --- Yuzme: bu ayki toplam saniye ---
    swim_history = load_swim_history(page)
    swim_total_seconds = 0.0
    for date_str, seconds in swim_history.items():
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if d.year == year and d.month == month:
            swim_total_seconds += seconds

    # --- Gym: bu ay icindeki haftalar (Pazartesisi bu ayda olan haftalar) ---
    gym_history = load_gym_history(page)
    qualifying_weeks = []
    cal = cal_module.Calendar(firstweekday=0)
    for week in cal.monthdayscalendar(year, month):
        first_day = next((d for d in week if d != 0), None)
        if first_day is None:
            continue
        monday_date = datetime(year, month, first_day) - timedelta(
            days=datetime(year, month, first_day).weekday()
        )
        if monday_date.month == month:
            iso = monday_date.isocalendar()
            week_key = f"{iso[0]}-W{iso[1]:02d}"
            if week_key not in qualifying_weeks:
                qualifying_weeks.append(week_key)

    gym_completed = 0
    gym_possible = len(qualifying_weeks) * len(names)
    per_workout_completed = [0] * len(names)
    # Haftanin gunune gore antrenman sayaci: {weekday_index: {workout_index: count}}
    weekday_workout_counts = {i: [0] * len(names) for i in range(7)}

    for week_key in qualifying_weeks:
        done_map = gym_history.get(week_key, {})
        for i in range(len(names)):
            entry = get_gym_entry(done_map, i)
            if entry["done"]:
                gym_completed += 1
                per_workout_completed[i] += 1
                if entry["date"]:
                    try:
                        d = datetime.strptime(entry["date"], "%Y-%m-%d")
                        weekday_workout_counts[d.weekday()][i] += 1
                    except ValueError:
                        pass

    weakest_workout = None
    if qualifying_weeks:
        min_count = min(per_workout_completed)
        if min_count < len(qualifying_weeks):
            weakest_index = per_workout_completed.index(min_count)
            weakest_workout = (names[weakest_index], min_count, len(qualifying_weeks))

    # En belirgin "hafta gunu - antrenman" eslesmesini bul
    best_weekday_pattern = None
    best_count = 1  # en az 2 kez tekrar etmeli, tek seferlik veriyi anlamli sayma
    for weekday_idx, counts in weekday_workout_counts.items():
        for workout_idx, count in enumerate(counts):
            if count > best_count:
                best_count = count
                best_weekday_pattern = (weekday_names[weekday_idx], names[workout_idx], count)

    return {
        "swim_total_seconds": swim_total_seconds,
        "gym_completed": gym_completed,
        "gym_possible": gym_possible,
        "weakest_workout": weakest_workout,
        "weekday_pattern": best_weekday_pattern,
    }


def compute_activity_dates(page: ft.Page):
    """Gym/yuzme/ekstra antrenman kaynaklarindan tum 'aktif' tarihleri birlestirir."""
    dates = set()
    gym_history = load_gym_history(page)
    if isinstance(gym_history, dict):
        for week_key, done_map in gym_history.items():
            if not isinstance(done_map, dict):
                continue
            for i in range(4):
                entry = get_gym_entry(done_map, i)
                if entry["done"] and entry["date"]:
                    dates.add(entry["date"])
    swim_history = load_swim_history(page)
    for d, secs in swim_history.items():
        if secs and secs > 0:
            dates.add(d)
    extra_history = load_extra_history(page)
    for d, entry in extra_history.items():
        if sum_extra_for_day(entry) > 0:
            dates.add(d)
    return dates


def compute_active_streak(active_dates):
    """Bugunden geriye dogru kesintisiz aktif gun sayisi."""
    day = datetime.now().date()
    streak = 0
    while day.strftime("%Y-%m-%d") in active_dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


def compute_days_since_last_activity(active_dates):
    if not active_dates:
        return None
    try:
        last = max(datetime.strptime(d, "%Y-%m-%d").date() for d in active_dates)
    except ValueError:
        return None
    return (datetime.now().date() - last).days


def compute_daily_recommendation(page: ft.Page, lang="tr"):
    """Basit kural tabanli gunluk oneri: dinlenme / eksik antrenman / yuzme / tebrik."""
    active_dates = compute_activity_dates(page)
    active_streak = compute_active_streak(active_dates)

    if active_streak >= 6:
        return {"type": "rest"}

    week_start = datetime.now().date() - timedelta(days=datetime.now().weekday())
    weekly = compute_weekly_gym_report(page, week_start, lang=lang)
    last_done_days = compute_last_done_per_workout(page)
    names = gym_workouts_for(lang)

    if weekly["missing_names"]:
        # En uzun suredir yapilmayan (ya da hic yapilmamis) eksik antrenmani sec
        best_choice = None
        best_days = -1
        for name in weekly["missing_names"]:
            idx = workout_name_to_index(name, lang)
            days_val = last_done_days[idx] if idx is not None and last_done_days[idx] is not None else 9999
            if days_val > best_days:
                best_days = days_val
                best_choice = name
        return {"type": "workout", "workout": best_choice or weekly["missing_names"][0]}

    swim_history = load_swim_history(page)
    days = [week_start + timedelta(days=i) for i in range(7)]
    week_swim_total = sum(swim_history.get(d.strftime("%Y-%m-%d"), 0) for d in days)
    if week_swim_total <= 0:
        return {"type": "swim"}

    return {"type": "all_done"}


def compute_longest_focus_streak(history: dict) -> int:
    if not history:
        return 0
    try:
        dates = sorted(
            datetime.strptime(d, "%Y-%m-%d").date()
            for d, s in history.items() if s and s > 0
        )
    except ValueError:
        return 0
    if not dates:
        return 0
    longest = 1
    current = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            current += 1
            longest = max(longest, current)
        elif (dates[i] - dates[i - 1]).days > 1:
            current = 1
    return max(longest, current)


def compute_best_focus_week(history: dict):
    week_totals = {}
    for d, s in history.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        wk = week_key_for_date(dt)
        week_totals[wk] = week_totals.get(wk, 0) + s
    if not week_totals:
        return 0.0
    return max(week_totals.values()) / 3600


def compute_best_gym_week(page: ft.Page):
    gym_history = load_gym_history(page)
    best_count = 0
    if isinstance(gym_history, dict):
        for week_key, done_map in gym_history.items():
            if not isinstance(done_map, dict):
                continue
            count = sum(1 for i in range(4) if get_gym_entry(done_map, i)["done"])
            best_count = max(best_count, count)
    return best_count


def trailing_moving_average(history_dict: dict, days_list, window: int):
    """Her gun icin, o gunu de icine alan gerileyen (trailing) `window` gunluk
    ortalamayi hesaplar (saat cinsinden). Gorunen araligin oncesindeki gunleri
    de history_dict'ten cekerek pürüzsüz bir baslangic saglar."""
    result = []
    for d in days_list:
        window_days = [d - timedelta(days=k) for k in range(window)]
        vals = [history_dict.get(wd.strftime("%Y-%m-%d"), 0) / 3600 for wd in window_days]
        result.append(sum(vals) / len(vals) if vals else 0)
    return result


def compute_week_match_count(page: ft.Page, week_start):
    try:
        history = load_match_history(page)
    except Exception:
        history = {}
    if not isinstance(history, dict):
        history = {}
    days = [week_start + timedelta(days=i) for i in range(7)]
    return sum(history.get(d.strftime("%Y-%m-%d"), 0) for d in days)


def is_weekly_recap_window():
    """Haftalik ozet raporu sadece Pazar gecesi 22:00-23:59 arasi gorunur."""
    now = datetime.now()
    return now.weekday() == 6 and now.hour in (22, 23)


def compute_weekly_recap(page: ft.Page, lang="tr"):
    """Bu haftanin kisa, profesyonel ozetini cikarir (sadece Pazar gecesi gosterilir)."""
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    days = [week_start + timedelta(days=i) for i in range(7)]

    focus_history = load_history(page)
    focus_week_seconds = sum(focus_history.get(d.strftime("%Y-%m-%d"), 0) for d in days)
    focus_week_hours = focus_week_seconds / 3600

    # Onceki 4 haftanin ortalamasi (bu hafta haric) -- karsilastirma icin
    past_totals = []
    for w in range(1, 5):
        past_start = week_start - timedelta(days=7 * w)
        past_days = [past_start + timedelta(days=i) for i in range(7)]
        past_sum = sum(focus_history.get(d.strftime("%Y-%m-%d"), 0) for d in past_days)
        if past_sum > 0:
            past_totals.append(past_sum / 3600)
    avg_past_focus_hours = sum(past_totals) / len(past_totals) if past_totals else None

    swim_history = load_swim_history(page)
    swim_week_hours = sum(swim_history.get(d.strftime("%Y-%m-%d"), 0) for d in days) / 3600

    weekly_gym = compute_weekly_gym_report(page, week_start, lang=lang)
    gym_completed = len(weekly_gym["completed_names"])
    gym_possible = gym_completed + len(weekly_gym["missing_names"])

    match_count = compute_week_match_count(page, week_start)

    try:
        weight_history = load_weight_history(page)
    except Exception:
        weight_history = {}
    weekly_weights = compute_weekly_weight_averages(weight_history, weeks_back=2)
    weight_change = None
    if len(weekly_weights) >= 2:
        weight_change = weekly_weights[-1][1] - weekly_weights[-2][1]

    try:
        active_dates = compute_activity_dates(page)
        active_streak = compute_active_streak(active_dates)
    except Exception:
        active_streak = 0

    weekday_names = weekday_names_for(lang)
    day_totals = []
    for d in days:
        ds = d.strftime("%Y-%m-%d")
        total_h = focus_history.get(ds, 0) / 3600 + swim_history.get(ds, 0) / 3600
        day_totals.append(total_h)
    best_day_name = None
    if any(t > 0 for t in day_totals):
        best_idx = day_totals.index(max(day_totals))
        best_day_name = weekday_names[best_idx]

    return {
        "focus_week_hours": focus_week_hours,
        "avg_past_focus_hours": avg_past_focus_hours,
        "swim_week_hours": swim_week_hours,
        "gym_completed": gym_completed,
        "gym_possible": gym_possible,
        "gym_missing": weekly_gym["missing_names"],
        "extra_count": weekly_gym["extra_count"],
        "match_count": match_count,
        "weight_change": weight_change,
        "active_streak": active_streak,
        "best_day_name": best_day_name,
    }


def compute_correlation_insights(page: ft.Page):
    """Gym/yuzme yapilan gunler ile yapilmayan gunlerdeki ortalama odaklanma
    suresini karsilastirir. Sadece gozlemsel bir karsilastirmadir."""
    focus_history = load_history(page)
    gym_history = load_gym_history(page)
    swim_history = load_swim_history(page)

    gym_active_dates = set()
    if isinstance(gym_history, dict):
        for week_key, done_map in gym_history.items():
            if not isinstance(done_map, dict):
                continue
            for i in range(4):
                entry = get_gym_entry(done_map, i)
                if entry["done"] and entry["date"]:
                    gym_active_dates.add(entry["date"])

    swim_active_dates = {d for d, s in swim_history.items() if s and s > 0}

    all_date_strs = set(focus_history.keys()) | gym_active_dates | swim_active_dates
    parsed_dates = []
    for d in all_date_strs:
        try:
            parsed_dates.append(datetime.strptime(d, "%Y-%m-%d").date())
        except ValueError:
            continue
    if not parsed_dates:
        return None

    start = min(parsed_dates)
    end = datetime.now().date()
    if (end - start).days > 364:
        start = end - timedelta(days=364)

    gym_day_focus, non_gym_day_focus = [], []
    swim_day_focus, non_swim_day_focus = [], []
    d = start
    while d <= end:
        ds = d.strftime("%Y-%m-%d")
        f = focus_history.get(ds, 0) / 3600
        (gym_day_focus if ds in gym_active_dates else non_gym_day_focus).append(f)
        (swim_day_focus if ds in swim_active_dates else non_swim_day_focus).append(f)
        d += timedelta(days=1)

    result = {}
    if len(gym_day_focus) >= 3 and len(non_gym_day_focus) >= 3:
        result["gym"] = (
            sum(gym_day_focus) / len(gym_day_focus),
            sum(non_gym_day_focus) / len(non_gym_day_focus),
        )
    if len(swim_day_focus) >= 3 and len(non_swim_day_focus) >= 3:
        result["swim"] = (
            sum(swim_day_focus) / len(swim_day_focus),
            sum(non_swim_day_focus) / len(non_swim_day_focus),
        )
    return result if result else None


def compute_yearly_report(page: ft.Page, lang="tr"):
    """Bu yilin genel bakisini cikarir: toplam odaklanma/yuzme, gym orani,
    en yogun ay, toplam mac."""
    year = datetime.now().year
    focus_history = load_history(page)
    swim_history = load_swim_history(page)

    year_focus_seconds = sum(s for d, s in focus_history.items() if d.startswith(f"{year}-"))
    year_swim_seconds = sum(s for d, s in swim_history.items() if d.startswith(f"{year}-"))

    total_completed = 0
    total_possible = 0
    for m in range(1, datetime.now().month + 1):
        monthly = compute_monthly_sports_report(page, lang=lang, year=year, month=m)
        total_completed += monthly["gym_completed"]
        total_possible += monthly["gym_possible"]

    month_totals = {}
    for d, s in focus_history.items():
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        if dt.year == year:
            month_totals[dt.month] = month_totals.get(dt.month, 0) + s
    best_month_idx = max(month_totals, key=month_totals.get) if month_totals else None
    best_month_hours = (month_totals[best_month_idx] / 3600) if best_month_idx else 0
    best_month_name = month_labels_for(lang)[best_month_idx - 1] if best_month_idx else None

    try:
        match_history = load_match_history(page)
    except Exception:
        match_history = {}
    if not isinstance(match_history, dict):
        match_history = {}
    year_match_count = sum(
        c for d, c in match_history.items()
        if isinstance(c, (int, float)) and d.startswith(f"{year}-")
    )

    has_data = year_focus_seconds > 0 or year_swim_seconds > 0 or total_possible > 0 or year_match_count > 0

    return {
        "year": year,
        "focus_hours": year_focus_seconds / 3600,
        "swim_hours": year_swim_seconds / 3600,
        "gym_completed": total_completed,
        "gym_possible": total_possible,
        "best_month_name": best_month_name,
        "best_month_hours": best_month_hours,
        "match_count": year_match_count,
        "has_data": has_data,
    }


# Buluttan senkronlanacak alanlar (tum anahtarlar tanimlandiktan sonra)
CLOUD_FIELDS = [
    ("history_json", HISTORY_KEY),
    ("swim_json", SWIM_KEY),
    ("gym_json", GYM_KEY),
    ("weight_json", WEIGHT_KEY),
    ("weight_goal_json", WEIGHT_GOAL_KEY),
    ("match_json", MATCH_KEY),
    ("extra_json", EXTRA_KEY),
    ("habits_list_json", "habits_list"),
    ("habits_completions_json", "habits_completions"),
    ("day_notes_json", DAY_NOTES_KEY),
    ("milestones_json", "celebrated_milestones"),
    ("longterm_goal_json", "longterm_goal"),
]


# ---------------------------------------------------------
# ARAYUZ KATMANI (yeni tasarim: "Buz" ve "Grafit + Kum" temalari)
# ---------------------------------------------------------
THEME_KEY = "ui_theme"
LANG_KEY = "lang"
LAST_TAB_KEY = "last_tab"
LAST_SPORT_TAB_KEY = "last_sport_tab"

THEMES = {
    "buz": {
        "name": "Buz",
        "dark": False,
        "bg": "#E8EEF4",
        "panel": "#FAFCFE",
        "ink": "#10202E",
        "ink_70": "#5A6773",
        "ink_45": "#8E979F",
        "line": "#E2E7EC",
        "accent": "#2F6E9E",
        "accent_soft": "#E3ECF4",
        "on_accent": "#FAFCFE",
        "positive": "#2E7D6B",
        "note": "#C98A3F",
        "primary_btn": "#10202E",
        "on_primary_btn": "#FAFCFE",
        "heat": [
            (1.0, "#DCE8F2", "#10202E", "#5A6773"),
            (2.0, "#C2D8EA", "#10202E", "#5A6773"),
            (3.0, "#9EC0DD", "#0E1B26", "#3E4A55"),
            (4.0, "#74A3CB", "#0E1B26", "#3E4A55"),
            (5.0, "#4C86B4", "#0E1B26", "#2C3742"),
            (6.0, "#2F6E9E", "#FFFFFF", "#E4EDF4"),
            (999.0, "#1E5480", "#FFFFFF", "#E4EDF4"),
        ],
        "heat_zero_ink": "#8E979F",
    },
    "kagit": {
        "name": "Kağıt + Mürekkep",
        "dark": False,
        "bg": "#EFEBE3",
        "panel": "#F8F6F1",
        "ink": "#1C1A17",
        "ink_70": "#5C574E",
        "ink_45": "#8A8479",
        "line": "#DFD9CD",
        "accent": "#A5622F",
        "accent_soft": "#EDE2D6",
        "on_accent": "#F8F6F1",
        "positive": "#3E6B4F",
        "note": "#8A7B3C",
        "primary_btn": "#1C1A17",
        "on_primary_btn": "#F8F6F1",
        "heat": [
            (1.0, "#E4DCCC", "#1C1A17", "#5C574E"),
            (2.0, "#D5C8B0", "#1C1A17", "#5C574E"),
            (3.0, "#C2AF8F", "#1C1A17", "#4A453D"),
            (4.0, "#A8916C", "#1C1A17", "#3A352E"),
            (5.0, "#8A6E45", "#F8F6F1", "#EDE7DC"),
            (6.0, "#6B5230", "#F8F6F1", "#EDE7DC"),
            (999.0, "#4A3520", "#F8F6F1", "#EDE7DC"),
        ],
        "heat_zero_ink": "#8A8479",
    },
    "karanlik": {
        # Papara'nin karanlik temasi referans alinarak: neredeyse siyah
        # zemin, koyu gri kartlar, beyaz/gri metin hiyerarsisi, mavi vurgu.
        "name": "Karanlık",
        "dark": True,
        "bg": "#0D0D0F",
        "panel": "#1C1C1E",
        "ink": "#F5F5F7",
        "ink_70": "#B8B8BD",
        "ink_45": "#87878D",
        "line": "#2C2C2F",
        "accent": "#5487C0",
        "accent_soft": "#26344A",
        "on_accent": "#FFFFFF",
        "positive": "#34C759",
        "note": "#E0B84A",
        "primary_btn": "#F5F5F7",
        "on_primary_btn": "#0D0D0F",
        "heat": [
            (1.0, "#1F2733", "#F5F5F7", "#B8B8BD"),
            (2.0, "#243247", "#F5F5F7", "#B8B8BD"),
            (3.0, "#2B4568", "#F5F5F7", "#C7D6E8"),
            (4.0, "#33588A", "#FFFFFF", "#DCE7F4"),
            (5.0, "#3E6EAC", "#FFFFFF", "#E4EDF8"),
            (6.0, "#4F86C7", "#FFFFFF", "#EAF1FA"),
            (999.0, "#5487C0", "#FFFFFF", "#EAF1FA"),
        ],
        "heat_zero_ink": "#87878D",
    },
}

FOCUS_PRESETS = [25, 45, 60, 90]

# Yeni arayuze ait metinler (eski TRANSLATIONS sozlugu de kullanilmaya devam eder)
UI_LABELS = {
    "tab_today": ("Bugün", "Today"),
    "tab_focus": ("Odak", "Focus"),
    "tab_sports": ("Spor", "Sports"),
    "tab_calendar": ("Takvim", "Calendar"),
    "tab_report": ("Karne", "Report"),
    "settings": ("Ayarlar", "Settings"),
    "streak": ("SERİ", "STREAK"),
    "today_upper": ("BUGÜN", "TODAY"),
    "goal": ("HEDEF", "GOAL"),
    "week_upper": ("HAFTA", "WEEK"),
    "habits": ("ALIŞKANLIKLAR", "HABITS"),
    "new_habit": ("Yeni alışkanlık", "New habit"),
    "days": ("gün", "days"),
    "gym_week": ("Gym · hafta", "Gym · week"),
    "swim_week": ("Yüzme · hafta", "Swim · week"),
    "weight_trend": ("Kilo · eğilim", "Weight · trend"),
    "habit_row": ("Alışkanlık", "Habits"),
    "missing": ("EKSİK", "MISSING"),
    "insight": ("İÇGÖRÜ", "INSIGHT"),
    "pattern": ("DESEN", "PATTERN"),
    "focus_upper": ("ODAK", "FOCUS"),
    "session_no": ("{n}. SEANS", "SESSION {n}"),
    "session_left": ("{n} DK SEANS · KALAN", "{n} MIN SESSION · LEFT"),
    "todays_sessions": ("BUGÜNÜN SEANSLARI", "TODAY'S SESSIONS"),
    "start": ("Başlat", "Start"),
    "stop": ("Durdur", "Stop"),
    "reset": ("Sıfırla", "Reset"),
    "focus_btn": ("{n} dk odak", "{n} min focus"),
    "hours_upper": ("SAAT", "HOURS"),
    "sports_week": ("SPOR · HAFTA {n}", "SPORTS · WEEK {n}"),
    "workouts_week": ("ANTRENMAN · BU HAFTA", "WORKOUTS · THIS WEEK"),
    "done_upper": ("TAMAM", "DONE"),
    "next_upper": ("SIRADA", "NEXT"),
    "never_done": ("HİÇ YAPILMADI", "NEVER DONE"),
    "waiting_days": ("{n} GÜNDÜR BEKLİYOR", "WAITING {n} DAYS"),
    "did_it": ("Yaptım", "Did it"),
    "extra_week": ("Ekstra · hafta", "Extra · week"),
    "add_extra": ("Ekstra antrenman ekle", "Add extra workout"),
    "this_week": ("BU HAFTA", "THIS WEEK"),
    "minutes": ("Dakika", "Minutes"),
    "add": ("Ekle", "Add"),
    "no_record": ("Kayıt yok", "No records"),
    "last_weight": ("SON ÖLÇÜM · KG", "LAST · KG"),
    "weight_today_instruction": (
        "Bugünkü kilonu gir ve kaydet",
        "Enter and save today's weight",
    ),
    "weight_enter_hint": ("Örn: 78.5", "e.g. 78.5"),
    "weight_history_label": ("SON ÖLÇÜMLER", "RECENT RECORDS"),
    "my_goal": ("HEDEFİM", "MY GOAL"),
    "goal_lose": ("Vermek", "Lose"),
    "goal_maintain": ("Korumak", "Maintain"),
    "goal_gain": ("Almak", "Gain"),
    "save": ("Kaydet", "Save"),
    "trend": ("EĞİLİM", "TREND"),
    "on_track": ("HEDEFLE UYUMLU", "ON TRACK"),
    "off_track": ("HEDEFTEN SAPMA", "OFF TRACK"),
    "matches_month": ("MAÇ · BU AY", "MATCHES · THIS MONTH"),
    "match_today": ("Bugün maç yaptım", "I played today"),
    "monthly_report": ("AYLIK RAPOR", "MONTHLY REPORT"),
    "weakest": ("EN ZAYIF", "WEAKEST"),
    "hours_month": ("SAAT · BU AY", "HOURS · THIS MONTH"),
    "week_short": ("HFT", "WK"),
    "best_day": ("EN İYİ GÜN", "BEST DAY"),
    "no_data_month": ("Bu ay henüz kayıt yok.", "No records this month yet."),
    "note_dot": ("Nokta = o güne yazılmış not", "Dot = note on that day"),
    "report_week": ("KARNE · HAFTA {n}", "REPORT · WEEK {n}"),
    "good_week": ("İYİ HAFTA", "GOOD WEEK"),
    "recovering": ("TOPARLANMA", "RECOVERING"),
    "hours_focus": ("SAAT ODAK", "HOURS FOCUS"),
    "appearance": ("GÖRÜNÜM", "APPEARANCE"),
    "statistics": ("İSTATİSTİKLER", "STATISTICS"),
    "reminder": ("GÜN SONU HATIRLATMASI", "END-OF-DAY REMINDER"),
    "reminder_on": ("Açık", "On"),
    "reminder_off": ("Kapalı", "Off"),
    "reminder_hour": ("Saat", "Hour"),
    "reminder_help": (
        "Bu saatten sonra uygulamayı açtığında günün özetini gösterir.",
        "Shows today's summary when you open the app after this hour.",
    ),
    "day_summary": ("Günün özeti", "Today's summary"),
    "all_clear": ("Bugün her şey tamam. İyi geceler.", "All done today. Good night."),
    "ok": ("Tamam", "OK"),
    "session_label_hint": ("Bu seans ne için? (isteğe bağlı)", "What is this session for? (optional)"),
    "label_breakdown": ("NEYE HARCANDI", "WHERE IT WENT"),
    "unlabeled": ("etiketsiz", "unlabeled"),
    "show_all": ("Tüm analizleri gör", "Show all insights"),
    "show_less": ("Daha az göster", "Show less"),
    "resumed": ("Devam eden seans geri yüklendi", "Running session restored"),
    "recommendation": ("BUGÜNÜN ÖNERİSİ", "TODAY'S SUGGESTION"),
    "active_streak": ("Aktif seri", "Active streak"),
    "last_activity": ("Son aktivite", "Last activity"),
    "today_short": ("bugün", "today"),
    "days_ago": ("{n} gün önce", "{n} days ago"),
    "habit_30": ("30 günde {n}", "{n} in 30 days"),
    "records": ("REKORLAR", "RECORDS"),
    "longest_streak": ("En uzun seri", "Longest streak"),
    "best_week": ("En iyi hafta", "Best week"),
    "best_gym_week": ("En iyi gym haftası", "Best gym week"),
    "matches_week": ("Maç · bu hafta", "Matches · week"),
    "corr_gym": ("KORELASYON · GYM", "CORRELATION · GYM"),
    "corr_swim": ("KORELASYON · YÜZME", "CORRELATION · SWIM"),
    "corr_note": (
        "Bu bir gözlemdir, nedensellik değil.",
        "This is an observation, not causation.",
    ),
    "recap": ("HAFTA ÖZETİ", "WEEK RECAP"),
    "yearly": ("{y} ÖZETİ", "{y} SUMMARY"),
    "focus_label": ("Odak", "Focus"),
    "swim_label": ("Yüzme", "Swim"),
    "gym_rate": ("Gym oranı", "Gym rate"),
    "busiest_month": ("En yoğun ay", "Busiest month"),
    "matches_label": ("Maç", "Matches"),
    "weight_weeks": ("HAFTALIK ORTALAMA", "WEEKLY AVERAGE"),
    "completed": ("TAMAMLANAN", "COMPLETED"),
    "missing_list": ("EKSİK KALAN", "MISSING"),
    "weekly_report": ("HAFTALIK RAPOR", "WEEKLY REPORT"),
    "none_yet": ("yok", "none"),
    "prev_week": ("Önceki hafta", "Previous week"),
    "next_week": ("Sonraki hafta", "Next week"),
    "decline_warning": ("DÜŞÜŞ UYARISI", "DECLINE ALERT"),
    "decline_text": (
        "Son 4 hafta ortalaman {a:.1f} saat, önceki 4 hafta {b:.1f} saatti (%{c:.0f}).",
        "Last 4 weeks average {a:.1f}h, previous 4 weeks {b:.1f}h ({c:.0f}%).",
    ),
    "longterm": ("UZUN VADELİ HEDEF", "LONG-TERM GOAL"),
    "longterm_progress": (
        "{cur:.1f} / {tgt:.0f} saat · %{pct:.0f} · {days} gün kaldı",
        "{cur:.1f} / {tgt:.0f} hours · {pct:.0f}% · {days} days left",
    ),
    "longterm_pace": (
        "Gereken tempo {req:.1f} s/hafta, senin temponuz {act:.1f} s/hafta.",
        "Required pace {req:.1f} h/week, your pace {act:.1f} h/week.",
    ),
    "longterm_reached": ("Hedefe ulaştın!", "Goal reached!"),
    "target_hours": ("Hedef saat", "Target hours"),
    "target_date": ("Bitiş tarihi (YYYY-AA-GG)", "End date (YYYY-MM-DD)"),
    "set_goal": ("Hedefi kaydet", "Save goal"),
    "delete_goal": ("Hedefi sil", "Delete goal"),
    "account": ("HESAP KODU", "ACCOUNT CODE"),
    "account_help": (
        "Bu kodu başka cihaza girerek aynı verilere ulaşırsın.",
        "Enter this code on another device to access the same data.",
    ),
    "enter_code": ("6 haneli kod", "6-digit code"),
    "use_code": ("Kodu kullan", "Use code"),
    "sync_now": ("Şimdi senkronize et", "Sync now"),
    "new_code": ("Yeni kod üret", "Generate new code"),
    "new_code_done": ("Yeni kod: {code}", "New code: {code}"),
    "storage_error": ("Kod kaydedilemedi, tekrar dene", "Could not save the code, try again"),
    "language": ("DİL", "LANGUAGE"),
    "heat_scale": ("ISI SKALASI", "HEAT SCALE"),
    "heat_theme": ("Tema (tek renk)", "Theme (single hue)"),
    "theme_label": ("TEMA", "THEME"),
    "danger": ("GÜNÜ SIFIRLA", "RESET DAY"),
    "reset_day_help": (
        "Seçili günün tüm verilerini siler: odak, spor, kilo, maç, alışkanlık, not.",
        "Deletes all data for the selected day: focus, sports, weight, match, habits, note.",
    ),
    "reset_day_btn": ("Bugünü sıfırla", "Reset today"),
    "alltime": ("TÜM ZAMANLAR", "ALL TIME"),
    "first_use": ("İlk kullanım", "First use"),
    "day_note": ("Günün notu", "Note of the day"),
    "note_hint": ("Bugün ne oldu?", "What happened today?"),
    "delete_note": ("Notu sil", "Delete note"),
    "close": ("Kapat", "Close"),
    "reset_this_day": ("Bu günü sıfırla", "Reset this day"),
    "sync_done": ("Senkronizasyon tamam", "Sync complete"),
    "code_applied": ("Kod uygulandı, veriler çekiliyor", "Code applied, pulling data"),
    "invalid_code": ("Geçersiz kod, 6 haneli sayı gir", "Invalid code, enter a 6-digit number"),
    "code_found": ("Kod bulundu, verilerin senkronize edildi", "Code found, your data was synced"),
    "code_missing": (
        "Bu kod bulutta yok, cihazındaki veriler bu koda kaydedilecek",
        "This code doesn't exist in the cloud yet, your device data will be saved to it",
    ),
    "saved": ("Kaydedildi", "Saved"),
    "congrats": ("Tebrikler!", "Congratulations!"),
    "awesome": ("Harika!", "Awesome!"),
}

# Tek renk skala; eski dort palet kaldirildi (yorum gerektirmeyen tek skala).
HEAT_SCALE_OPTIONS = ["tema"]


def load_lang(page: ft.Page) -> str:
    try:
        val = page.client_storage.get(LANG_KEY)
    except Exception:
        val = None
    return val if val in ("tr", "en") else "tr"


def save_lang(page: ft.Page, code: str):
    try:
        page.client_storage.set(LANG_KEY, code)
    except Exception:
        pass


def load_int_pref(page: ft.Page, key: str, default: int, lo: int, hi: int) -> int:
    try:
        val = page.client_storage.get(key)
        val = int(val)
    except Exception:
        return default
    return val if lo <= val <= hi else default


def save_int_pref(page: ft.Page, key: str, value: int):
    try:
        page.client_storage.set(key, int(value))
    except Exception:
        pass


def load_theme_key(page: ft.Page) -> str:
    val = None
    for attempt in range(3):
        try:
            val = page.client_storage.get(THEME_KEY)
            break
        except Exception:
            val = None
            if attempt < 2:
                time.sleep(0.05)
    return val if val in THEMES else "buz"


def save_theme_key(page: ft.Page, key: str):
    # ONEMLI: hesap kodunda oldugu gibi client_storage.set bazi cihazlarda
    # sessizce basarisiz olabiliyor -- tema secimi kaybolup uygulama
    # kapanip acilinca "buz" temasina geri donuyordu. Birkac kez deniyoruz.
    for attempt in range(3):
        try:
            page.client_storage.set(THEME_KEY, key)
            return
        except Exception:
            if attempt < 2:
                time.sleep(0.05)


def load_heat_scale(page: ft.Page) -> str:
    return "tema"


def heat_cell_colors(theme, seconds, scale="tema"):
    """Sureye gore (zemin, gun rengi, alt yazi rengi) dondurur."""
    if seconds is None or seconds <= 0:
        return (None, theme["heat_zero_ink"], theme["heat_zero_ink"])
    if scale != "tema":
        bg = heat_color_for_seconds(seconds, scale)
        return (bg, "#FFFFFF", "#E6E6E6")
    hours = seconds / 3600.0
    for limit, bg, ink, sub in theme["heat"]:
        if hours <= limit:
            return (bg, ink, sub)
    last = theme["heat"][-1]
    return (last[1], last[2], last[3])


def fmt_clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def fmt_hm(seconds):
    if seconds is None or seconds <= 0:
        return "0:00"
    total_min = int(round(seconds / 60.0))
    return f"{total_min // 60}:{total_min % 60:02d}"


def fmt_short(seconds):
    if seconds is None or seconds <= 0:
        return ""
    hours = seconds / 3600.0
    if hours >= 1:
        return f"{hours:.1f}s"
    return f"{int(round(seconds / 60.0))}dk"


def day_key(d=None):
    d = d or datetime.now().date()
    return d.strftime("%Y-%m-%d")


def week_dates(offset_weeks=0):
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset_weeks)
    return [monday + timedelta(days=i) for i in range(7)]


# --- Gun sonu hatirlatmasi ---
REMINDER_ON_KEY = "reminder_on"
REMINDER_HOUR_KEY = "reminder_hour"
REMINDER_SHOWN_KEY = "reminder_shown"


def load_reminder_settings(page: ft.Page):
    try:
        on = page.client_storage.get(REMINDER_ON_KEY)
        on = True if on is None else bool(on)
    except Exception:
        on = True
    try:
        hour = int(page.client_storage.get(REMINDER_HOUR_KEY))
    except Exception:
        hour = 21
    if not 6 <= hour <= 23:
        hour = 21
    return on, hour


def save_reminder_settings(page: ft.Page, on: bool, hour: int):
    try:
        page.client_storage.set(REMINDER_ON_KEY, bool(on))
        page.client_storage.set(REMINDER_HOUR_KEY, int(hour))
    except Exception:
        pass


def reminder_already_shown(page: ft.Page, date_str: str) -> bool:
    try:
        return page.client_storage.get(REMINDER_SHOWN_KEY) == date_str
    except Exception:
        return False


def mark_reminder_shown(page: ft.Page, date_str: str):
    try:
        page.client_storage.set(REMINDER_SHOWN_KEY, date_str)
    except Exception:
        pass


# --- Calisan seansin kalici durumu (uygulama kapansa da surer) ---
RUN_STATE_KEY = "focus_run_state"


def save_run_state(page: ft.Page, minutes: int, started_at: float, label: str):
    try:
        page.client_storage.set(
            RUN_STATE_KEY,
            json.dumps({"min": int(minutes), "start": float(started_at), "label": label or ""}),
        )
    except Exception:
        pass


def load_run_state(page: ft.Page):
    try:
        raw = page.client_storage.get(RUN_STATE_KEY)
        data = json.loads(raw) if raw else None
        if isinstance(data, dict) and "start" in data and "min" in data:
            return data
    except Exception:
        pass
    return None


def clear_run_state(page: ft.Page):
    try:
        page.client_storage.remove(RUN_STATE_KEY)
    except Exception:
        pass


# --- Seans etiketleri ---
def load_session_labels(page: ft.Page):
    try:
        raw = page.client_storage.get("session_labels")
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def remember_session_label(page: ft.Page, label: str):
    label = (label or "").strip()
    if not label:
        return
    labels = load_session_labels(page)
    if label in labels:
        labels.remove(label)
    labels.insert(0, label)
    try:
        page.client_storage.set("session_labels", json.dumps(labels[:8]))
    except Exception:
        pass


def label_totals_for_week(page: ft.Page, offset=0):
    """Secili haftanin etiket bazli odak kirilimi: {etiket: saniye}."""
    totals = {}
    for d in week_dates(offset):
        for s in load_sessions(page, d.strftime("%Y-%m-%d")):
            key = (s.get("label") or "").strip()
            totals[key] = totals.get(key, 0) + int(s.get("sec", 0))
    return totals


_SESSIONS_CACHE = {}  # date_str -> list -- gunluk seans listesi bellekte de tutuluyor


def load_sessions(page: ft.Page, date_str: str):
    # ONEMLI: bu fonksiyon haftalik etiket kirilimi icin GUN BASINA (ör. hafta
    # icin 7 kez) cagriliyor. Onbellek olmadan her cagri ayri bir
    # client_storage yuvarlama-gidis-donusu demekti -- Karne sekmesine her
    # girildiginde/her yenilendiginde 7 senkron cihaz cagrisi tetikleniyordu,
    # bu da o sekmede belirgin bir kasmaya yol aciyordu. Diger tum
    # gecmis-veri fonksiyonlarinda (_load_cached_or_remote) zaten olan aynı
    # bellek onbellegi mantigini buraya da ekliyoruz.
    if date_str in _SESSIONS_CACHE:
        return _SESSIONS_CACHE[date_str]
    try:
        raw = page.client_storage.get("sessions_" + date_str)
        data = json.loads(raw) if raw else []
        data = data if isinstance(data, list) else []
    except Exception:
        data = []
    _SESSIONS_CACHE[date_str] = data
    return data


def add_session(page: ft.Page, date_str: str, seconds: float, label: str = ""):
    sessions = load_sessions(page, date_str)
    sessions.append(
        {"t": datetime.now().strftime("%H:%M"), "sec": int(seconds), "label": (label or "").strip()}
    )
    _SESSIONS_CACHE[date_str] = sessions
    try:
        page.client_storage.set("sessions_" + date_str, json.dumps(sessions))
    except Exception:
        pass


def clear_sessions(page: ft.Page, date_str: str):
    _SESSIONS_CACHE.pop(date_str, None)
    try:
        page.client_storage.remove("sessions_" + date_str)
    except Exception:
        pass


# ---------------------------------------------------------
# TEMEL BILESENLER
# ---------------------------------------------------------
def label_text(theme, value, color=None, size=10):
    return ft.Text(value, size=size, weight=ft.FontWeight.W_500, color=color or theme["ink_45"])


def big_number(theme, value, size=58, color=None):
    return ft.Text(
        value, size=size, weight=ft.FontWeight.W_500, color=color or theme["ink"], no_wrap=True
    )


def panel(theme, controls, padding=None):
    return ft.Container(
        content=ft.Column(controls, spacing=0, tight=True),
        bgcolor=theme["panel"],
        border_radius=18,
        padding=padding if padding is not None else ft.Padding.symmetric(vertical=0, horizontal=16),
    )


def data_row(theme, label, value, value_color=None, trailing=None, last=False):
    right = [
        ft.Text(value, size=15, weight=ft.FontWeight.W_500, color=value_color or theme["ink"])
    ]
    if trailing is not None:
        right.insert(0, trailing)
    return ft.Container(
        content=ft.Row(
            [
                ft.Text(label, size=13.5, color=theme["ink_70"]),
                ft.Row(right, spacing=9, alignment=ft.MainAxisAlignment.END),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        padding=ft.Padding.symmetric(vertical=12, horizontal=0),
        border=None if last else ft.Border.only(bottom=ft.BorderSide(1, theme["line"])),
    )


def soft_block(theme, kicker, text, tone="accent"):
    tone_color = theme.get(tone, theme["accent"])
    return ft.Container(
        content=ft.Column(
            [label_text(theme, kicker, tone_color), ft.Text(text, size=13, color=theme["ink_70"])],
            spacing=5,
            tight=True,
        ),
        bgcolor=ft.Colors.with_opacity(0.12, tone_color),
        border_radius=16,
        padding=ft.Padding.symmetric(vertical=12, horizontal=15),
    )


def section(theme, title, controls, open_state, on_toggle, accent=False):
    """Katlanabilir ayar bolumu."""
    head = ft.Container(
        content=ft.Row(
            [
                label_text(theme, title, theme["accent"] if accent else theme["ink_45"]),
                ft.Icon(
                    ft.Icons.EXPAND_LESS if open_state else ft.Icons.EXPAND_MORE,
                    size=18,
                    color=theme["ink_45"],
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        padding=ft.Padding.symmetric(vertical=13, horizontal=0),
        on_click=on_toggle,
        ink=True,
    )
    items = [head]
    if open_state:
        items += [ft.Container(height=4)] + controls + [ft.Container(height=12)]
    return ft.Container(
        content=ft.Column(items, spacing=0, tight=True),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme["line"])),
    )


def pill(theme, text, active=False, on_click=None, expand=True, width=None):
    return ft.Container(
        content=ft.Text(
            text,
            size=12.5,
            weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_500,
            color=theme["on_accent"] if active else theme["ink_70"],
            text_align=ft.TextAlign.CENTER,
        ),
        bgcolor=theme["accent"] if active else theme["accent_soft"],
        border_radius=999,
        padding=ft.Padding.symmetric(vertical=9, horizontal=0),
        alignment=ft.Alignment.CENTER,
        expand=expand,
        width=width,
        on_click=on_click,
        ink=True,
        animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT),
    )


def primary_button(theme, text, on_click, expand=True, width=None):
    return ft.Container(
        content=ft.Text(
            text,
            size=13.5,
            weight=ft.FontWeight.W_600,
            color=theme["on_primary_btn"],
            text_align=ft.TextAlign.CENTER,
        ),
        bgcolor=theme["primary_btn"],
        border_radius=999,
        padding=ft.Padding.symmetric(vertical=15, horizontal=0),
        alignment=ft.Alignment.CENTER,
        expand=expand,
        width=width,
        on_click=on_click,
        ink=True,
    )


def quiet_button(theme, text, on_click, expand=True, width=None):
    return ft.Container(
        content=ft.Text(
            text,
            size=13.5,
            weight=ft.FontWeight.W_500,
            color=theme["ink_70"],
            text_align=ft.TextAlign.CENTER,
        ),
        bgcolor=theme["panel"],
        border=None if theme["dark"] else ft.Border.all(1, theme["line"]),
        border_radius=999,
        padding=ft.Padding.symmetric(vertical=15, horizontal=0),
        alignment=ft.Alignment.CENTER,
        expand=expand,
        width=width,
        on_click=on_click,
        ink=True,
    )


def ring(theme, value, size, center, stroke=3, control=None):
    if control is None:
        control = ft.ProgressRing(
            width=size,
            height=size,
            stroke_width=stroke,
            color=theme["accent"],
            bgcolor=ft.Colors.with_opacity(0.10, theme["ink"]),
        )
    control.value = max(0.0, min(1.0, value))
    return ft.Container(
        width=size,
        height=size,
        content=ft.Stack(
            [
                ft.Container(width=size, height=size, content=control),
                ft.Container(
                    width=size, height=size, content=center, alignment=ft.Alignment.CENTER
                ),
            ]
        ),
    )


def week_bars(theme, values, height=34, highlight_index=None):
    peak = max(values) if values and max(values) > 0 else 1
    bars = []
    for i, v in enumerate(values):
        h = max(4, (v / peak) * height)
        active = highlight_index is not None and i == highlight_index
        bars.append(
            ft.Container(
                height=h,
                bgcolor=theme["accent"] if active else ft.Colors.with_opacity(0.13, theme["ink"]),
                border_radius=3,
                expand=True,
            )
        )
    return ft.Row(bars, spacing=6, vertical_alignment=ft.CrossAxisAlignment.END, height=height)


# ---------------------------------------------------------
# UYGULAMA
# ---------------------------------------------------------
class OrganizerApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.theme_key = load_theme_key(page)
        self.lang = load_lang(page)
        self.heat_scale = load_heat_scale(page)
        self.tab = load_int_pref(page, LAST_TAB_KEY, 0, 0, 4)
        self.sport_tab = load_int_pref(page, LAST_SPORT_TAB_KEY, 0, 0, 3)
        self.cal_offset = 0
        self.week_offset = 0
        self.settings_open = False
        self.show_all_insights = False
        self.open_sections = {"account": True}
        self.focus_minutes = 45
        self.focus_remaining = 45 * 60
        self.focus_running = False
        self.focus_started_at = None
        self.focus_label = ""
        self.focus_thread = None
        self.clock_text = None
        self.focus_ring = None
        self._swipe_dx = 0.0
        self.body_shift = None  # mount() icinde ft.Container olarak kurulur
        # ONEMLI: burada eskiden ft.AnimatedSwitcher (FADE) kullaniliyordu.
        # Sorun su: refresh() UYGULAMADAKI HEMEN HEMEN HER ETKILESIMDE
        # cagriliyor (tema degistirme, ayar kaydetme, bolum acma/kapama,
        # kronometre bitisi, vs.) -- ve her seferinde self.body.content
        # YENIDEN olusturuluyordu. AnimatedSwitcher her yeni content'i
        # "degisti" sayip otomatik olarak fade animasyonu baslatiyordu; yani
        # sadece sekme gecislerinde degil, UYGULAMANIN HER YERINDE surekli
        # kisa bir yanip-sonme animasyonu tetikleniyordu. Bu, genel "kasma/
        # donma" hissinin buyuk kismindan sorumluydu. Simdi sekmeler arasi
        # yonlu kayma animasyonunu SADECE body_shift (asagida) veriyor, ve
        # SADECE gercekten sekme degisince calisiyor -- diger tum refresh()
        # cagrilari artik anlik/animasyonsuz, bu da uygulamayi genel olarak
        # cok daha akici hissettiriyor.
        self.body = ft.Container(content=ft.Container(), expand=True)
        self.milestone_queue = []
        self.note_field = ft.TextField(multiline=True, min_lines=2, max_lines=4, dense=True)
        self.selected_date = day_key()

    @property
    def theme(self):
        return THEMES[self.theme_key]

    def t(self, key, **kwargs):
        pair = UI_LABELS.get(key)
        if not pair:
            return key
        text = pair[1] if self.lang == "en" else pair[0]
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text

    # -- durum yardimcilari ------------------------------------
    def today(self):
        return day_key()

    def focus_seconds_today(self):
        return load_history(self.page).get(self.today(), 0)

    def week_focus_values(self, offset=0):
        history = load_history(self.page)
        return [history.get(d.strftime("%Y-%m-%d"), 0) for d in week_dates(offset)]

    def gym_state(self):
        history = load_gym_history(self.page)
        done_map = history.get(current_week_key(), {})
        return [get_gym_entry(done_map, i) for i in range(4)]

    def swim_week_seconds(self, offset=0):
        history = load_swim_history(self.page)
        return sum(history.get(d.strftime("%Y-%m-%d"), 0) for d in week_dates(offset))

    def extra_week_count(self, offset=0):
        history = load_extra_history(self.page)
        return sum(sum_extra_for_day(history.get(d.strftime("%Y-%m-%d"))) for d in week_dates(offset))

    def toast(self, message):
        sb = ft.SnackBar(content=ft.Text(message))
        try:
            self.page.open(sb)
            return
        except Exception:
            pass
        try:
            self.page.snack_bar = sb
            sb.open = True
            self.page.update()
        except Exception:
            pass

    # -- yenileme ----------------------------------------------
    def refresh(self):
        theme = self.theme
        self.page.bgcolor = theme["bg"]
        self.page.theme_mode = ft.ThemeMode.DARK if theme["dark"] else ft.ThemeMode.LIGHT
        self.body.content = self.build_screen()
        # Alt navigasyon cubugundaki secili ikon, sekme swipe ile (nav
        # cubuguna dokunulmadan) degistiginde de HER ZAMAN gercek sekmeyle
        # uyumlu olsun diye burada senkronize ediyoruz -- eskiden bu satir
        # yoktu, bu yuzden parmakla kaydirinca icerik degisiyor ama alt
        # sekme cubugu eski sekmede kalmis gibi gorunuyordu.
        self.nav.selected_index = self.tab
        self.nav.bgcolor = theme["panel"]
        self.nav.indicator_color = ft.Colors.with_opacity(0.16, theme["accent"])
        for i, key in enumerate(
            ["tab_today", "tab_focus", "tab_sports", "tab_calendar", "tab_report"]
        ):
            self.nav.destinations[i].label = self.t(key)
        self.header.content = self.build_header()
        self.page.update()

    def set_tab(self, i, animate_slide=True):
        direction = 1 if i > self.tab else (-1 if i < self.tab else 0)
        self.settings_open = False
        self.tab = i
        save_int_pref(self.page, LAST_TAB_KEY, i)
        self.refresh()
        # Alt navigasyon cubugundan (veya swipe birakilisindan) sekme
        # degisince, Papara/benzeri uygulamalardaki gibi yeni icerik dogru
        # yonden kayarak gelsin. Swipe'ta da GARANTILI olarak bu animasyonu
        # tetikliyoruz (animate_slide=True) -- parmak takibi (on_swipe_update)
        # bazi cihazlarda gecikmeli/eksik calisabiliyor, o durumda bile
        # birakinca en azindan bu net kayma animasyonu mutlaka gorulsun diye.
        if animate_slide and direction != 0 and self.body_shift is not None:
            try:
                self.page.run_task(self._slide_in_new_tab, direction)
            except Exception:
                pass

    async def _slide_in_new_tab(self, direction):
        """Yeni sekme icerigini sagdan/soldan kaydirarak icine getirir."""
        if getattr(self, "_tab_anim_running", False):
            return
        self._tab_anim_running = True
        try:
            enter_from = 0.30 if direction > 0 else -0.30
            self.body_shift.animate_offset = None
            self.body_shift.offset = ft.Offset(enter_from, 0)
            self.body_shift.update()
            # NOT: bu bekleme, "animasyonsuz sicrama" komutunun gercekten
            # cihaza ulasip bir kare cizilmesi icin gerekli -- cok kisa
            # olursa (ör. 20ms) iki update() ust uste binip sadece SONUNCUSU
            # islenebiliyor, bu da kaymanin hic gorunmemesine yol aciyordu.
            await asyncio.sleep(0.07)
            self.body_shift.animate_offset = ft.Animation(200, ft.AnimationCurve.EASE_OUT_CUBIC)
            self.body_shift.offset = ft.Offset(0, 0)
            self.body_shift.update()
        except Exception:
            pass
        finally:
            self._tab_anim_running = False

    # -- sekmeler arasi kaydirma (swipe) --------------------------
    # Icerik parmagi CANLI takip ediyor (on_swipe_update sirasinda aninda
    # kayiyor, animasyonsuz) ve birakildiginda yumusakca merkeze donuyor.
    # ONEMLI: eskiden burada max_px=90 diye sabit bir tavan vardi -- yani
    # parmak ekranin yarisini kat etse bile icerik sadece 90 piksel
    # kayiyordu. Bu "gercekten parmagi takip etmiyor, sadece hafif bir
    # ipucu veriyor" hissi yaratiyordu (kullanicinin "Papara'daki gibi TAM
    # anlamiyla takip etsin" istegi tam da buydu). Simdi tavan ekranin
    # kendi genisligi -- yani parmak ekranin tamamini kat edince icerik de
    # neredeyse tamamen kenara kayiyor, gercek 1:1 bir surukleme.
    def on_swipe_update(self, e):
        dx = getattr(e, "delta_x", None)
        if dx is None:
            local_delta = getattr(e, "local_delta", None)
            dx = getattr(local_delta, "x", 0) if local_delta is not None else 0
        self._swipe_dx += dx or 0
        if self.settings_open or self.body_shift is None:
            return
        at_first = self.tab == 0
        at_last = self.tab == 4
        raw = self._swipe_dx
        # ilk/son sekmede daha ileri kaydirmaya karsi "lastik" direnci
        if (raw > 0 and at_first) or (raw < 0 and at_last):
            raw *= 0.35
        try:
            width = self.body_shift.width or self.page.width or 340
        except Exception:
            width = 340
        max_px = max(width - 24, 60)  # kenarlarda hafif bosluk birakiyoruz
        shift_px = max(-max_px, min(max_px, raw))
        # asiri sik update() cagrisini onlemek icin kucuk degisimleri atla --
        # 5px'e cikardik: cok hizli kaydirmalarda saniyede onlarca update()
        # cagrisi cihaza gidip gelmeye calisiyor, bu da genel kasmaya
        # katkida bulunuyordu. 5px goz ile fark edilmeyecek kadar kucuk
        # ama cagri sayisini belirgin sekilde azaltiyor.
        last_sent = getattr(self, "_last_shift_px", 0)
        if abs(shift_px - last_sent) < 5 and shift_px not in (-max_px, max_px):
            return
        self._last_shift_px = shift_px
        # surukleme sirasinda animasyon KAPALI: parmakla birebir, aninda takip
        self.body_shift.animate_offset = None
        self.body_shift.offset = ft.Offset(shift_px / max(width, 1), 0)
        try:
            self.body_shift.update()
        except Exception:
            pass

    def on_swipe_end(self, e):
        dx = self._swipe_dx
        self._swipe_dx = 0.0
        self._last_shift_px = 0
        if self.body_shift is not None:
            # birakinca: yumusak bir yayla merkeze don -- bu, sekme
            # degisimiyle birlikte zaten yonlu bir kayma hissi veriyor
            self.body_shift.animate_offset = ft.Animation(220, ft.AnimationCurve.EASE_OUT_CUBIC)
            self.body_shift.offset = ft.Offset(0, 0)
            try:
                self.body_shift.update()
            except Exception:
                pass
        if self.settings_open:
            return
        threshold = 60
        if dx <= -threshold:
            # sola kaydirma -> sonraki sekme
            self.set_tab(min(self.tab + 1, 4), animate_slide=True)
        elif dx >= threshold:
            # saga kaydirma -> onceki sekme
            self.set_tab(max(self.tab - 1, 0), animate_slide=True)

    def toggle_theme(self, e=None):
        self.theme_key = "kagit" if self.theme_key == "buz" else "buz"
        save_theme_key(self.page, self.theme_key)
        self.refresh()

    def open_settings(self, e=None):
        self.settings_open = True
        self.refresh()

    # -- ust bar ------------------------------------------------
    def build_header(self):
        theme = self.theme
        return ft.Row(
            [
                label_text(
                    theme,
                    self.t("settings").upper() if self.settings_open else theme["name"].upper(),
                    theme["accent"],
                ),
                ft.Row(
                    [
                        ft.IconButton(
                            ft.Icons.CONTRAST,
                            icon_size=18,
                            icon_color=theme["ink_70"],
                            tooltip=self.t("theme_label"),
                            on_click=self.toggle_theme,
                        ),
                        ft.IconButton(
                            ft.Icons.CLOSE if self.settings_open else ft.Icons.SETTINGS,
                            icon_size=18,
                            icon_color=theme["ink_70"],
                            tooltip=self.t("settings"),
                            on_click=(lambda e: self.set_tab(self.tab))
                            if self.settings_open
                            else self.open_settings,
                        ),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

    # -- ekran secici -------------------------------------------
    def build_screen(self):
        if self.settings_open:
            return self.build_settings_screen()
        if self.tab == 0:
            return self.build_today_screen()
        if self.tab == 1:
            return self.build_focus_screen()
        if self.tab == 2:
            return self.build_sports_screen()
        if self.tab == 3:
            return self.build_calendar_screen()
        return self.build_report_screen()

    # ---------------- BUGUN -----------------------------------
    def build_today_screen(self):
        theme = self.theme
        secs = self.focus_seconds_today()
        goal = 4 * 3600
        streak = compute_focus_streak(load_history(self.page))
        gym = self.gym_state()
        gym_done = sum(1 for g in gym if g["done"])
        weights = load_weight_history(self.page)
        last_weight = weights[sorted(weights.keys())[-1]] if weights else None
        stale = compute_last_done_per_workout(self.page)
        stale_text = None
        for i, days in enumerate(stale):
            if days is None or days >= 8:
                stale_text = gym_workouts_for(self.lang)[i] + (
                    " — " + self.t("never_done").lower()
                    if days is None
                    else " — " + self.t("waiting_days", n=days).lower()
                )
                break

        active_dates = compute_activity_dates(self.page)
        active_streak = compute_active_streak(active_dates)
        days_since = compute_days_since_last_activity(active_dates)
        rec = compute_daily_recommendation(self.page, self.lang)
        if rec["type"] == "workout":
            rec_text = tr(self.lang, "rec_workout", workout=rec["workout"])
        else:
            rec_text = tr(self.lang, "rec_" + rec["type"])

        head = ft.Row(
            [
                label_text(
                    theme,
                    datetime.now().strftime("%d.%m")
                    + " · "
                    + self.t("week_upper")
                    + " "
                    + str(datetime.now().isocalendar()[1]),
                    theme["accent"],
                ),
                ft.Container(
                    content=ft.Row(
                        [
                            label_text(theme, self.t("streak"), theme["accent"]),
                            ft.Text(
                                str(streak), size=11, weight=ft.FontWeight.W_600, color=theme["ink"]
                            ),
                        ],
                        spacing=5,
                        tight=True,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.14, theme["accent"]),
                    border_radius=999,
                    padding=ft.Padding.symmetric(vertical=5, horizontal=11),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        center = ft.Column(
            [
                big_number(theme, fmt_hm(secs), 62),
                label_text(
                    theme,
                    f"{self.t('today_upper')} · %{int(min(100, secs / goal * 100))} · {self.t('goal')} 4:00",
                    theme["ink_45"],
                ),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

        rows = [
            data_row(
                theme,
                self.t("gym_week"),
                f"{gym_done}/4",
                trailing=ft.Row(
                    [
                        ft.Container(
                            width=15,
                            height=4,
                            border_radius=2,
                            bgcolor=theme["accent"]
                            if i < gym_done
                            else ft.Colors.with_opacity(0.16, theme["ink"]),
                        )
                        for i in range(4)
                    ],
                    spacing=4,
                ),
            ),
            data_row(theme, self.t("swim_week"), fmt_hm(self.swim_week_seconds())),
            data_row(
                theme,
                self.t("weight_trend"),
                f"{last_weight:.1f} kg" if last_weight else "—",
                value_color=theme["positive"] if last_weight else theme["ink_45"],
            ),
            data_row(
                theme,
                self.t("active_streak"),
                f"{active_streak} {self.t('days')}",
            ),
            data_row(
                theme,
                self.t("last_activity"),
                self.t("today_short")
                if days_since == 0
                else (self.t("days_ago", n=days_since) if days_since is not None else "—"),
                last=True,
            ),
        ]

        content = [
            head,
            ft.Container(height=12),
            ft.Text(
                tr(self.lang, get_greeting_key()),
                size=20,
                weight=ft.FontWeight.W_500,
                color=theme["ink"],
            ),
            ft.Container(height=6),
            ft.Container(
                content=ring(theme, secs / goal, 212, center), alignment=ft.Alignment.CENTER
            ),
            ft.Container(height=18),
            week_bars(theme, self.week_focus_values(), highlight_index=datetime.now().weekday()),
            ft.Container(height=18),
            panel(theme, rows),
        ]
        content += [
            ft.Container(height=10),
            soft_block(theme, self.t("recommendation"), rec_text, "positive"),
        ]
        if stale_text:
            content += [ft.Container(height=10), soft_block(theme, self.t("missing"), stale_text)]
        goal_progress = compute_long_term_goal_progress(self.page)
        if goal_progress:
            content += [
                ft.Container(height=10),
                soft_block(
                    theme,
                    self.t("longterm"),
                    self.t("longterm_reached")
                    if goal_progress["reached"]
                    else self.t(
                        "longterm_progress",
                        cur=goal_progress["current_hours"],
                        tgt=goal_progress["target_hours"],
                        pct=goal_progress["pct"],
                        days=goal_progress["days_remaining"],
                    ),
                    "positive" if goal_progress["on_track"] else "accent",
                ),
            ]
        # ONEMLI: burada "giris sekmesi"nin (Bugun) en altinda 25 dk / 45 dk
        # hizli-baslat butonlari vardi -- kullanici istegiyle kaldirildi,
        # odaklanma suresi zaten Odak sekmesinden seciliyor.
        content += [ft.Container(height=8)]
        return ft.Column(content, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)

    # ---------------- ODAK ------------------------------------
    def build_focus_screen(self):
        theme = self.theme
        total = self.focus_minutes * 60
        progress = 1 - (self.focus_remaining / total if total else 0)
        sessions = load_sessions(self.page, self.today())

        self.clock_text = big_number(theme, fmt_clock(self.focus_remaining), 50)
        self.focus_ring = ft.ProgressRing(
            width=236,
            height=236,
            stroke_width=3,
            color=theme["accent"],
            bgcolor=ft.Colors.with_opacity(0.10, theme["ink"]),
        )
        center = ft.Column(
            [
                self.clock_text,
                label_text(theme, self.t("session_left", n=self.focus_minutes), theme["ink_45"]),
            ],
            spacing=12,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

        self.label_field = ft.TextField(
            value=self.focus_label,
            hint_text=self.t("session_label_hint"),
            dense=True,
            expand=True,
            on_change=self.on_label_change,
        )
        recent_labels = load_session_labels(self.page)[:4]
        label_row = ft.Row(
            [
                pill(
                    theme,
                    lbl,
                    active=(lbl == self.focus_label),
                    on_click=lambda e, l=lbl: self.on_pick_label(l),
                )
                for lbl in recent_labels
            ],
            spacing=6,
            wrap=True,
        )

        session_rows = [
            data_row(
                theme,
                self.t("todays_sessions"),
                fmt_hm(self.focus_seconds_today()),
                last=len(sessions) == 0,
            )
        ]
        recent = sessions[-5:]
        for i, s in enumerate(recent):
            lbl = (s.get("label") or "").strip()
            session_rows.append(
                data_row(
                    theme,
                    s.get("t", "") + (f" · {lbl}" if lbl else ""),
                    f"{int(s.get('sec', 0) / 60)} dk" if self.lang == "tr" else f"{int(s.get('sec', 0) / 60)} min",
                    last=(i == len(recent) - 1),
                )
            )

        return ft.Column(
            [
                ft.Row(
                    [
                        label_text(theme, self.t("focus_upper"), theme["accent"]),
                        ft.Container(
                            content=label_text(
                                theme, self.t("session_no", n=len(sessions) + 1), theme["accent"]
                            ),
                            bgcolor=ft.Colors.with_opacity(0.14, theme["accent"]),
                            border_radius=999,
                            padding=ft.Padding.symmetric(vertical=5, horizontal=11),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(height=16),
                ft.Container(
                    content=ring(theme, progress, 236, center, control=self.focus_ring),
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Container(height=16),
                ft.Row(
                    [
                        primary_button(
                            theme,
                            self.t("stop") if self.focus_running else self.t("start"),
                            self.on_toggle_focus,
                        ),
                        quiet_button(
                            theme, self.t("reset"), self.on_reset_focus, expand=False, width=110
                        ),
                    ],
                    spacing=8,
                ),
                ft.Container(height=12),
                ft.Row(
                    [
                        pill(
                            theme,
                            str(m),
                            active=(m == self.focus_minutes),
                            on_click=lambda e, mm=m: self.set_focus_minutes(mm),
                        )
                        for m in FOCUS_PRESETS
                    ],
                    spacing=7,
                ),
                ft.Container(height=14),
                self.label_field,
                ft.Container(height=8) if recent_labels else ft.Container(height=0),
                label_row,
                ft.Container(height=16),
                panel(theme, session_rows),
                ft.Container(height=16),
                week_bars(
                    theme, self.week_focus_values(), highlight_index=datetime.now().weekday()
                ),
                ft.Container(height=8),
                label_text(
                    theme,
                    f"{self.t('week_upper')} · {sum(self.week_focus_values()) / 3600:.1f} {self.t('hours_upper')}",
                    theme["ink_45"],
                ),
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def on_label_change(self, e):
        self.focus_label = (e.control.value or "").strip()
        if self.focus_running and self.focus_started_at:
            save_run_state(self.page, self.focus_minutes, self.focus_started_at, self.focus_label)

    def on_pick_label(self, label):
        self.focus_label = "" if self.focus_label == label else label
        if self.focus_running and self.focus_started_at:
            save_run_state(self.page, self.focus_minutes, self.focus_started_at, self.focus_label)
        self.refresh()

    def set_focus_minutes(self, minutes):
        if self.focus_running:
            return
        self.focus_minutes = minutes
        self.focus_remaining = minutes * 60
        self.refresh()

    def start_focus(self, minutes, label=""):
        self.focus_minutes = minutes
        self.focus_remaining = minutes * 60
        self.focus_label = label or self.focus_label
        self.tab = 1
        save_int_pref(self.page, LAST_TAB_KEY, 1)
        self.settings_open = False
        self.focus_running = True
        self.focus_started_at = time.time()
        save_run_state(self.page, minutes, self.focus_started_at, self.focus_label)
        self.spawn_timer()
        self.refresh()

    def restore_run_state(self):
        """Uygulama kapaninca duran seansi geri yukler."""
        state = load_run_state(self.page)
        if not state:
            return
        minutes = int(state.get("min", 45))
        started = float(state.get("start", 0))
        elapsed = time.time() - started
        total = minutes * 60
        self.focus_minutes = minutes
        self.focus_label = state.get("label", "")
        if elapsed >= total:
            # seans yokken tamamlanmis: kaydet ve kapat
            add_focus_seconds(self.page, self.today(), total)
            add_session(self.page, self.today(), total, self.focus_label)
            clear_run_state(self.page)
            self.focus_running = False
            self.focus_remaining = total
            self.focus_started_at = None
            return
        self.focus_remaining = int(total - elapsed)
        self.focus_started_at = started
        self.focus_running = True
        self.tab = 1
        self.spawn_timer()

    def on_toggle_focus(self, e):
        if self.focus_running:
            self.focus_running = False
            if self.focus_started_at:
                elapsed = min(time.time() - self.focus_started_at, self.focus_minutes * 60)
            else:
                elapsed = self.focus_minutes * 60 - self.focus_remaining
            clear_run_state(self.page)
            self.focus_started_at = None
            if elapsed > 0:
                add_focus_seconds(self.page, self.today(), elapsed)
                add_session(self.page, self.today(), elapsed, self.focus_label)
                remember_session_label(self.page, self.focus_label)
            # ONEMLI: burada eskiden "self.focus_remaining = self.focus_minutes * 60"
            # vardi -- yani DUR'a basinca ekran hemen basa (ör. 45:00) sifirlaniyordu.
            # focus_remaining zaten _timer_tick tarafindan surekli guncelleniyor,
            # oldugu gibi birakiyoruz ki "44:32'de durdurdum, 44:32'de kalsin"
            # beklentisi karsilansin. Tekrar "Basla"ya basildiginda (asagidaki
            # else) zaten bu kalan sureden devam ediyor.
            self.refresh()
            self.check_milestones()
        else:
            self.focus_running = True
            self.focus_started_at = time.time() - (self.focus_minutes * 60 - self.focus_remaining)
            save_run_state(self.page, self.focus_minutes, self.focus_started_at, self.focus_label)
            self.spawn_timer()
            self.refresh()

    def on_reset_focus(self, e):
        self.focus_running = False
        self.focus_started_at = None
        clear_run_state(self.page)
        self.focus_remaining = self.focus_minutes * 60
        self.refresh()

    def spawn_timer(self):
        if getattr(self, "_timer_running_flag", False):
            return

        # page.run_task, gorevi sayfanin KENDI asyncio event loop'unda
        # calistirir (page.run_thread gibi ayri bir OS thread'inde degil).
        # page.run_thread + saniye basi page.update() denendiginde bazi
        # cihazlarda deger dogru hesaplaniyor ama ekrana YANSIMIYORDU --
        # yalnizca kullanici bir yere dokunup baska bir olay tetikleyince
        # (o da kendi event loop turunda) guncelleniyordu. Sayfayla ayni
        # event loop'ta calisan bir async gorev bu sorunu kokten cozuyor:
        # kronometre artik ekrana hic dokunmadan, kesintisiz akiyor.
        try:
            self.page.run_task(self._timer_loop_async)
            return
        except Exception:
            pass

        # eski Flet surumleri icin geri dusme (thread tabanli)
        def run():
            self._timer_running_flag = True
            try:
                self._timer_loop_sync()
            finally:
                self._timer_running_flag = False

        try:
            self.page.run_thread(run)
        except Exception:
            self.focus_thread = threading.Thread(target=run, daemon=True)
            self.focus_thread.start()

    async def _timer_loop_async(self):
        self._timer_running_flag = True
        try:
            while self.focus_running and self.focus_remaining > 0:
                await asyncio.sleep(1)
                if not self.focus_running:
                    return
                self._timer_tick()
        finally:
            self._timer_running_flag = False
        self._timer_finish()

    def _timer_loop_sync(self):
        while self.focus_running and self.focus_remaining > 0:
            time.sleep(1)
            if not self.focus_running:
                return
            self._timer_tick()
        self._timer_finish()

    def _timer_tick(self):
        """Her saniye: kalan sureyi hesaplar ve (Odak sekmesindeyse) ekrani
        gunceller. Hem async hem senkron dongu tarafindan kullanilir."""
        if self.focus_started_at:
            self.focus_remaining = max(
                0,
                int(self.focus_minutes * 60 - (time.time() - self.focus_started_at)),
            )
        else:
            self.focus_remaining -= 1
        if self.tab == 1 and not self.settings_open and self.clock_text is not None:
            try:
                total = self.focus_minutes * 60
                self.clock_text.value = fmt_clock(self.focus_remaining)
                if self.focus_ring is not None:
                    self.focus_ring.value = 1 - (
                        self.focus_remaining / total if total else 0
                    )
                self.clock_text.update()
                if self.focus_ring is not None:
                    self.focus_ring.update()
            except Exception:
                pass

    def _timer_finish(self):
        if self.focus_running and self.focus_remaining <= 0:
            self.focus_running = False
            clear_run_state(self.page)
            self.focus_started_at = None
            add_focus_seconds(self.page, self.today(), self.focus_minutes * 60)
            add_session(self.page, self.today(), self.focus_minutes * 60, self.focus_label)
            remember_session_label(self.page, self.focus_label)
            self.focus_remaining = self.focus_minutes * 60
            try:
                self.refresh()
                self.check_milestones()
            except Exception:
                pass

    # ---------------- SPOR ------------------------------------
    def build_sports_screen(self):
        theme = self.theme
        tab_names = (
            ["Gym", "Yüzme", "Kilo", "Maç"]
            if self.lang == "tr"
            else ["Gym", "Swim", "Weight", "Match"]
        )
        tabs = ft.Row(
            [
                pill(
                    theme,
                    name,
                    active=(i == self.sport_tab),
                    on_click=lambda e, idx=i: self.set_sport_tab(idx),
                )
                for i, name in enumerate(tab_names)
            ],
            spacing=6,
        )
        if self.sport_tab == 0:
            detail = self.build_gym_section()
        elif self.sport_tab == 1:
            detail = self.build_swim_section()
        elif self.sport_tab == 2:
            detail = self.build_weight_section()
        else:
            detail = self.build_match_section()

        return ft.Column(
            [
                label_text(
                    theme,
                    self.t("sports_week", n=datetime.now().isocalendar()[1]),
                    theme["accent"],
                ),
                ft.Container(height=14),
                tabs,
                ft.Container(height=20),
                detail,
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def set_sport_tab(self, i):
        self.sport_tab = i
        save_int_pref(self.page, LAST_SPORT_TAB_KEY, i)
        self.refresh()

    def build_gym_section(self):
        theme = self.theme
        gym = self.gym_state()
        done_count = sum(1 for g in gym if g["done"])
        stale = compute_last_done_per_workout(self.page)
        cards = []
        for i, name in enumerate(gym_workouts_for(self.lang)):
            entry = gym[i]
            if entry["done"]:
                cards.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.CHECK_CIRCLE, size=21, color=theme["accent"]),
                                ft.Column(
                                    [
                                        ft.Text(
                                            name,
                                            size=14,
                                            weight=ft.FontWeight.W_500,
                                            color=theme["ink"],
                                        ),
                                        label_text(
                                            theme,
                                            (entry["date"] or "").replace("-", ".")
                                            + " · "
                                            + self.t("done_upper"),
                                            theme["ink_45"],
                                        ),
                                    ],
                                    spacing=3,
                                    tight=True,
                                    expand=True,
                                ),
                            ],
                            spacing=12,
                        ),
                        bgcolor=theme["panel"],
                        border_radius=16,
                        padding=ft.Padding.symmetric(vertical=14, horizontal=15),
                    )
                )
            else:
                days = stale[i]
                overdue = days is None or days >= 8
                cards.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(
                                    ft.Icons.RADIO_BUTTON_UNCHECKED,
                                    size=21,
                                    color=theme["ink_45"],
                                ),
                                ft.Column(
                                    [
                                        ft.Text(
                                            name,
                                            size=14,
                                            weight=ft.FontWeight.W_500,
                                            color=theme["ink"],
                                        ),
                                        label_text(
                                            theme,
                                            self.t("next_upper")
                                            if not overdue
                                            else (
                                                self.t("never_done")
                                                if days is None
                                                else self.t("waiting_days", n=days)
                                            ),
                                            theme["accent"] if overdue else theme["ink_45"],
                                        ),
                                    ],
                                    spacing=3,
                                    tight=True,
                                    expand=True,
                                ),
                                pill(
                                    theme,
                                    self.t("did_it"),
                                    active=overdue,
                                    on_click=lambda e, idx=i: self.on_gym_done(idx),
                                    expand=False,
                                    width=84,
                                ),
                            ],
                            spacing=12,
                        ),
                        bgcolor=ft.Colors.with_opacity(0.10, theme["accent"]) if overdue else None,
                        border=ft.Border.all(
                            1,
                            ft.Colors.with_opacity(0.5, theme["accent"])
                            if overdue
                            else theme["line"],
                        ),
                        border_radius=16,
                        padding=ft.Padding.symmetric(vertical=13, horizontal=14),
                        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
                    )
                )

        monthly = compute_monthly_sports_report(self.page, self.lang)
        monthly_blocks = []
        if monthly.get("weakest_workout"):
            wname, wcount, wtotal = monthly["weakest_workout"]
            monthly_blocks.append(
                soft_block(theme, self.t("weakest"), f"{wname} · {wcount}/{wtotal}", "accent")
            )
        if monthly.get("weekday_pattern"):
            wd, wk, cnt = monthly["weekday_pattern"]
            monthly_blocks.append(
                soft_block(
                    theme,
                    self.t("pattern"),
                    f"{wd} · {wk} ({cnt}x)",
                    "positive",
                )
            )

        return ft.Column(
            [
                ft.Row(
                    [
                        big_number(theme, f"{done_count}/4", 52),
                        label_text(theme, self.t("workouts_week"), theme["ink_45"]),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                ft.Container(height=12),
                ft.Row(
                    [
                        ft.Container(
                            height=5,
                            border_radius=3,
                            expand=True,
                            bgcolor=theme["accent"]
                            if i < done_count
                            else ft.Colors.with_opacity(0.16, theme["ink"]),
                        )
                        for i in range(4)
                    ],
                    spacing=5,
                ),
                ft.Container(height=18),
                ft.Column(cards, spacing=9),
                ft.Container(height=14),
                panel(
                    theme,
                    [
                        data_row(theme, self.t("extra_week"), str(self.extra_week_count())),
                        data_row(
                            theme, self.t("swim_week"), fmt_hm(self.swim_week_seconds()), last=True
                        ),
                    ],
                ),
                ft.Container(height=14),
                primary_button(theme, self.t("add_extra"), self.on_add_extra),
                ft.Container(height=16),
                label_text(theme, self.t("monthly_report"), theme["ink_45"]),
                ft.Container(height=8),
                ft.Column(monthly_blocks, spacing=9),
                ft.Container(height=8),
            ],
            spacing=0,
            tight=True,
        )

    def on_gym_done(self, index):
        mark_gym_done(self.page, current_week_key(), index, self.today())
        self.refresh()
        self.check_milestones()

    def on_add_extra(self, e):
        types = extra_types_for(self.lang)
        self.open_choice_dialog(
            self.t("add_extra"),
            types,
            lambda name: (
                add_extra_workout(self.page, self.today(), name),
                self.close_dialog(),
                self.refresh(),
            ),
        )

    def build_swim_section(self):
        theme = self.theme
        history = load_swim_history(self.page)
        keys = sorted(history.keys(), reverse=True)[:6]
        rows = [
            data_row(theme, k.replace("-", "."), fmt_hm(history[k]), last=(i == len(keys) - 1))
            for i, k in enumerate(keys)
        ]
        if not rows:
            rows = [data_row(theme, self.t("no_record"), "—", last=True)]
        self.swim_field = ft.TextField(hint_text=self.t("minutes"), dense=True, expand=True)
        return ft.Column(
            [
                ft.Row(
                    [
                        big_number(theme, fmt_hm(self.swim_week_seconds()), 52),
                        label_text(theme, self.t("this_week"), theme["ink_45"]),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                ft.Container(height=18),
                ft.Row(
                    [
                        self.swim_field,
                        primary_button(
                            theme, self.t("add"), self.on_add_swim, expand=False, width=100
                        ),
                    ],
                    spacing=8,
                ),
                ft.Container(height=18),
                panel(theme, rows),
            ],
            spacing=0,
            tight=True,
        )

    def on_add_swim(self, e):
        try:
            minutes = float((self.swim_field.value or "0").replace(",", "."))
        except ValueError:
            minutes = 0
        if minutes > 0:
            add_swim_seconds(self.page, self.today(), minutes * 60)
        self.refresh()
        self.check_milestones()

    def build_weight_section(self):
        theme = self.theme
        history = load_weight_history(self.page)
        keys = sorted(history.keys(), reverse=True)[:6]
        rows = [
            data_row(theme, k.replace("-", "."), f"{history[k]:.1f} kg", last=(i == len(keys) - 1))
            for i, k in enumerate(keys)
        ]
        if not rows:
            rows = [data_row(theme, self.t("no_record"), "—", last=True)]

        goal = load_weight_goal(self.page)
        goal_pills = ft.Row(
            [
                pill(
                    theme,
                    self.t("goal_" + key),
                    active=(goal == key),
                    on_click=lambda e, k=key: self.on_set_weight_goal(k),
                )
                for key in ["lose", "maintain", "gain"]
            ],
            spacing=6,
        )

        trend = compute_weight_trend_and_prediction(history)
        blocks = []
        if trend and trend.get("slope_per_week") is not None:
            slope = trend["slope_per_week"]
            pred = trend.get("prediction_next_week")
            if self.lang == "en":
                line = f"Weekly change {slope:+.2f} kg."
                if pred:
                    line += f" Next week forecast {pred:.1f} kg."
            else:
                line = f"Haftalık değişim {slope:+.2f} kg."
                if pred:
                    line += f" Gelecek hafta tahmini {pred:.1f} kg."
            kicker, tone = self.t("trend"), "positive"
            if goal:
                on_track = (
                    (goal == "lose" and slope < -0.05)
                    or (goal == "gain" and slope > 0.05)
                    or (goal == "maintain" and abs(slope) <= 0.15)
                )
                kicker = self.t("on_track") if on_track else self.t("off_track")
                tone = "positive" if on_track else "accent"
            blocks = [ft.Container(height=14), soft_block(theme, kicker, line, tone)]

        weekly_avgs = compute_weekly_weight_averages(history, weeks_back=6)
        if len(weekly_avgs) >= 2:
            avg_rows = [
                data_row(
                    theme,
                    wk,
                    f"{kg:.1f} kg",
                    last=(i == len(weekly_avgs) - 1),
                )
                for i, (wk, kg) in enumerate(reversed(weekly_avgs))
            ]
            blocks += [
                ft.Container(height=14),
                label_text(theme, self.t("weight_weeks"), theme["ink_45"]),
                ft.Container(height=8),
                panel(theme, avg_rows),
            ]

        self.weight_field = ft.TextField(
            hint_text=self.t("weight_enter_hint"),
            dense=True,
            expand=True,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        latest = f"{history[keys[0]]:.1f}" if keys else "—"
        return ft.Column(
            [
                ft.Row(
                    [
                        big_number(theme, latest, 52),
                        label_text(theme, self.t("last_weight"), theme["ink_45"]),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                ft.Container(height=16),
                label_text(theme, self.t("weight_today_instruction"), theme["ink_45"]),
                ft.Container(height=8),
                ft.Row(
                    [
                        self.weight_field,
                        primary_button(
                            theme, self.t("save"), self.on_save_weight, expand=False, width=100
                        ),
                    ],
                    spacing=8,
                ),
                ft.Container(height=18),
                label_text(theme, self.t("my_goal"), theme["ink_45"]),
                ft.Container(height=8),
                goal_pills,
                ft.Container(height=18),
                label_text(theme, self.t("weight_history_label"), theme["ink_45"]),
                ft.Container(height=8),
                panel(theme, rows),
            ]
            + blocks,
            spacing=0,
            tight=True,
        )

    def on_set_weight_goal(self, direction):
        save_weight_goal(self.page, direction)
        self.refresh()

    def on_save_weight(self, e):
        try:
            kg = float((self.weight_field.value or "0").replace(",", "."))
        except ValueError:
            kg = 0
        if kg > 0:
            set_weight_entry(self.page, self.today(), kg)
        self.refresh()

    def build_match_section(self):
        theme = self.theme
        history = load_match_history(self.page)
        month_prefix = datetime.now().strftime("%Y-%m")
        month_count = sum(
            v if isinstance(v, int) else 0
            for k, v in history.items()
            if k.startswith(month_prefix)
        )
        keys = sorted(history.keys(), reverse=True)[:6]
        rows = [
            data_row(theme, k.replace("-", "."), str(history[k]), last=(i == len(keys) - 1))
            for i, k in enumerate(keys)
        ]
        if not rows:
            rows = [data_row(theme, self.t("no_record"), "—", last=True)]
        return ft.Column(
            [
                ft.Row(
                    [
                        big_number(theme, str(month_count), 52),
                        label_text(theme, self.t("matches_month"), theme["ink_45"]),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                ft.Container(height=18),
                primary_button(theme, self.t("match_today"), self.on_add_match),
                ft.Container(height=18),
                panel(theme, rows),
            ],
            spacing=0,
            tight=True,
        )

    def on_add_match(self, e):
        add_match(self.page, self.today())
        self.refresh()
        self.check_milestones()

    # ---------------- TAKVIM ----------------------------------
    def build_calendar_screen(self):
        theme = self.theme
        history = load_history(self.page)
        notes = load_day_notes(self.page)
        base = datetime.now().date().replace(day=1)
        month = base.month + self.cal_offset
        year = base.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        days_in_month = cal_module.monthrange(year, month)[1]
        lead = datetime(year, month, 1).date().weekday()

        header_cells = [
            ft.Container(
                width=36,
                content=ft.Text(
                    lbl,
                    size=9,
                    weight=ft.FontWeight.W_500,
                    color=theme["ink_45"],
                    text_align=ft.TextAlign.CENTER,
                ),
            )
            for lbl in weekday_labels_for(self.lang)
        ]
        header_cells.append(
            ft.Container(
                width=42,
                content=ft.Text(
                    self.t("week_short"),
                    size=9,
                    weight=ft.FontWeight.W_500,
                    color=theme["accent"],
                    text_align=ft.TextAlign.CENTER,
                ),
            )
        )

        cells = [None] * lead + list(range(1, days_in_month + 1))
        while len(cells) % 7:
            cells.append(None)
        rows = []
        month_total = 0
        for w in range(0, len(cells), 7):
            week = cells[w : w + 7]
            week_total = 0
            row_items = []
            for day in week:
                if day is None:
                    row_items.append(ft.Container(width=36, height=45))
                    continue
                key = f"{year:04d}-{month:02d}-{day:02d}"
                secs = history.get(key, 0)
                week_total += secs
                bg, ink, sub = heat_cell_colors(theme, secs, self.heat_scale)
                is_today = key == self.today()
                stack = [
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text(
                                    str(day), size=11.5, weight=ft.FontWeight.W_500, color=ink
                                ),
                                ft.Text(fmt_short(secs), size=8, color=sub),
                            ],
                            spacing=1,
                            alignment=ft.MainAxisAlignment.CENTER,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            tight=True,
                        ),
                        alignment=ft.Alignment.CENTER,
                        expand=True,
                    )
                ]
                if notes.get(key):
                    stack.append(
                        ft.Container(
                            width=5, height=5, border_radius=3, bgcolor=theme["note"], right=4, top=4
                        )
                    )
                row_items.append(
                    ft.Container(
                        width=36,
                        height=45,
                        bgcolor=bg,
                        border_radius=9,
                        border=ft.Border.all(
                            1.5 if is_today else 1,
                            theme["accent"] if is_today else theme["line"],
                        ),
                        content=ft.Stack(stack),
                        on_click=lambda e, k=key: self.open_day_dialog(k),
                        ink=True,
                    )
                )
            month_total += week_total
            row_items.append(
                ft.Container(
                    width=42,
                    height=45,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        fmt_short(week_total) or "·",
                        size=9.5,
                        weight=ft.FontWeight.W_500,
                        color=theme["accent"],
                    ),
                )
            )
            rows.append(ft.Row(row_items, spacing=2))

        best_day, best_secs = None, 0
        for k, v in history.items():
            if k.startswith(f"{year:04d}-{month:02d}") and v > best_secs:
                best_day, best_secs = k, v

        legend_items = []
        if self.heat_scale == "tema":
            picks = [theme["heat"][0], theme["heat"][1], theme["heat"][3], theme["heat"][6]]
            legend_specs = [(p[0], p[1]) for p in picks]
        else:
            legend_specs = [
                (1.0, heat_color_for_seconds(3600, self.heat_scale)),
                (2.0, heat_color_for_seconds(2 * 3600, self.heat_scale)),
                (4.0, heat_color_for_seconds(4 * 3600, self.heat_scale)),
                (999.0, heat_color_for_seconds(7 * 3600, self.heat_scale)),
            ]
        for limit, bg in legend_specs:
            legend_items.append(
                ft.Row(
                    [
                        ft.Container(width=11, height=11, border_radius=3, bgcolor=bg),
                        ft.Text(
                            "6s+" if limit > 900 else f"{int(limit)}s",
                            size=10,
                            color=theme["ink_45"],
                        ),
                    ],
                    spacing=5,
                    tight=True,
                )
            )

        return ft.Column(
            [
                ft.Row(
                    [
                        ft.IconButton(
                            ft.Icons.CHEVRON_LEFT,
                            icon_color=theme["ink_70"],
                            on_click=lambda e: self.shift_month(-1),
                        ),
                        ft.Text(
                            f"{month_labels_for(self.lang)[month - 1]} {year}",
                            size=17,
                            weight=ft.FontWeight.W_500,
                            color=theme["ink"],
                        ),
                        ft.IconButton(
                            ft.Icons.CHEVRON_RIGHT,
                            icon_color=theme["ink_70"],
                            on_click=lambda e: self.shift_month(1),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(height=12),
                ft.Row(
                    [
                        big_number(theme, f"{month_total / 3600:.1f}", 44),
                        label_text(theme, self.t("hours_month"), theme["ink_45"]),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.END,
                    spacing=8,
                ),
                ft.Container(height=16),
                ft.Container(
                    content=ft.Column(
                        [ft.Row(header_cells, spacing=2), ft.Container(height=6)] + rows,
                        spacing=3,
                        tight=True,
                    ),
                    bgcolor=theme["panel"],
                    border_radius=18,
                    padding=ft.Padding.symmetric(vertical=12, horizontal=10),
                ),
                ft.Container(height=14),
                ft.Row(legend_items, spacing=10, wrap=True),
                ft.Container(height=14),
                soft_block(
                    theme,
                    self.t("best_day"),
                    f"{best_day.replace('-', '.')} · {best_secs / 3600:.1f} "
                    + self.t("hours_upper").lower()
                    if best_day
                    else self.t("no_data_month"),
                    "positive",
                ),
                ft.Container(height=10),
                ft.Row(
                    [
                        ft.Container(width=5, height=5, border_radius=3, bgcolor=theme["note"]),
                        ft.Text(self.t("note_dot"), size=11, color=theme["ink_45"]),
                    ],
                    spacing=7,
                ),
                ft.Container(height=8),
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def shift_month(self, delta):
        self.cal_offset += delta
        self.refresh()

    # ---------------- GUN PENCERESI ---------------------------
    def open_day_dialog(self, date_str):
        theme = self.theme
        self.selected_date = date_str
        notes = load_day_notes(self.page)
        history = load_history(self.page)
        self.note_field = ft.TextField(
            value=notes.get(date_str, ""),
            hint_text=self.t("note_hint"),
            multiline=True,
            min_lines=2,
            max_lines=4,
            dense=True,
        )
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                date_str.replace("-", ".") + " · " + fmt_hm(history.get(date_str, 0)),
                size=15,
                weight=ft.FontWeight.W_600,
            ),
            content=ft.Column(
                [
                    ft.Text(self.t("day_note"), size=12, color=theme["ink_45"]),
                    self.note_field,
                ],
                tight=True,
                spacing=8,
            ),
            actions=[
                ft.TextButton(self.t("delete_note"), on_click=lambda e: self.on_delete_note()),
                ft.TextButton(
                    self.t("reset_this_day"),
                    on_click=lambda e: self.on_reset_day(date_str),
                    style=ft.ButtonStyle(color=theme["accent"]),
                ),
                ft.TextButton(self.t("close"), on_click=lambda e: self.close_dialog()),
                ft.ElevatedButton(self.t("save"), on_click=lambda e: self.on_save_note()),
            ],
        )
        self.show_dialog(dlg)

    def on_save_note(self):
        set_day_note(self.page, self.selected_date, (self.note_field.value or "").strip())
        self.close_dialog()
        self.refresh()

    def on_delete_note(self):
        delete_day_note(self.page, self.selected_date)
        self.close_dialog()
        self.refresh()

    def on_reset_day(self, date_str):
        reset_day_data(self.page, date_str)
        clear_sessions(self.page, date_str)
        if date_str == self.today():
            clear_run_state(self.page)
            self.focus_running = False
            self.focus_started_at = None
            self.focus_remaining = self.focus_minutes * 60
        self.close_dialog()
        self.refresh()
        self.toast(self.t("saved"))

    # ---------------- KARNE -----------------------------------
    def build_report_screen(self):
        theme = self.theme
        history = load_history(self.page)
        values = self.week_focus_values(self.week_offset)
        total = sum(values)
        prior = [sum(self.week_focus_values(w)) for w in range(-8, 0)]
        active = [v for v in prior if v > 0]
        avg = (sum(active) / len(active)) if active else 0
        delta = total - avg
        week_start = week_dates(self.week_offset)[0]
        wreport = compute_weekly_gym_report(self.page, week_start, self.lang)
        gym_done = len(wreport["completed_names"])

        blocks = []
        corr = compute_correlation_insights(self.page)
        if corr:
            if corr.get("gym"):
                a, b = corr["gym"]
                blocks.append(
                    soft_block(
                        theme,
                        self.t("corr_gym"),
                        (
                            f"Gym yaptığın günlerde ortalama odak {a:.1f} saat, "
                            f"yapmadığın günlerde {b:.1f} saat."
                            if self.lang == "tr"
                            else f"On gym days you average {a:.1f}h focus, on other days {b:.1f}h."
                        )
                        + " "
                        + self.t("corr_note"),
                        "positive",
                    )
                )
            if corr.get("swim"):
                a, b = corr["swim"]
                blocks.append(
                    soft_block(
                        theme,
                        self.t("corr_swim"),
                        f"Yüzdüğün günlerde ortalama odak {a:.1f} saat, diğer günlerde {b:.1f} saat."
                        if self.lang == "tr"
                        else f"On swim days you average {a:.1f}h focus, on other days {b:.1f}h.",
                        "ink_45",
                    )
                )
        weekday_totals = [0.0] * 7
        weekday_counts = [0] * 7
        for k, v in history.items():
            try:
                d = datetime.strptime(k, "%Y-%m-%d").date()
            except ValueError:
                continue
            weekday_totals[d.weekday()] += v
            weekday_counts[d.weekday()] += 1
        best_wd, best_avg = None, 0
        for i in range(7):
            if weekday_counts[i]:
                a = weekday_totals[i] / weekday_counts[i]
                if a > best_avg:
                    best_wd, best_avg = i, a
        if best_wd is not None:
            wd_name = weekday_names_for(self.lang)[best_wd]
            blocks.append(
                soft_block(
                    theme,
                    self.t("pattern"),
                    f"{wd_name} en verimli günün — ortalama {best_avg / 3600:.1f} saat."
                    if self.lang == "tr"
                    else f"{wd_name} is your best day — {best_avg / 3600:.1f}h average.",
                    "ink_45",
                )
            )
        stale = compute_last_done_per_workout(self.page)
        for i, days in enumerate(stale):
            if days is None or days >= 8:
                blocks.append(
                    soft_block(
                        theme,
                        self.t("missing"),
                        gym_workouts_for(self.lang)[i]
                        + (
                            " · " + self.t("never_done").lower()
                            if days is None
                            else f" · {days} " + self.t("days")
                        ),
                    )
                )
                break

        decline = compute_long_term_trend(self.page)
        if decline:
            blocks.append(
                soft_block(
                    theme,
                    self.t("decline_warning"),
                    self.t(
                        "decline_text",
                        a=decline["recent_avg"],
                        b=decline["prior_avg"],
                        c=decline["change_pct"],
                    ),
                    "accent",
                )
            )

        goal_progress = compute_long_term_goal_progress(self.page)
        if goal_progress:
            blocks.append(
                soft_block(
                    theme,
                    self.t("longterm"),
                    (
                        self.t("longterm_reached")
                        if goal_progress["reached"]
                        else self.t(
                            "longterm_progress",
                            cur=goal_progress["current_hours"],
                            tgt=goal_progress["target_hours"],
                            pct=goal_progress["pct"],
                            days=goal_progress["days_remaining"],
                        )
                    )
                    + " "
                    + self.t(
                        "longterm_pace",
                        req=goal_progress["required_pace_per_week"],
                        act=goal_progress["actual_pace_per_week"],
                    ),
                    "positive" if goal_progress["on_track"] else "accent",
                )
            )

        if self.week_offset == 0 and datetime.now().weekday() == 6:
            recap = compute_weekly_recap(self.page, self.lang)
            parts = [
                f"{recap['focus_week_hours']:.1f} " + self.t("hours_upper").lower(),
                f"Gym {recap['gym_completed']}/{recap['gym_possible']}",
            ]
            if recap["swim_week_hours"] > 0:
                parts.append(f"{self.t('swim_label')} {recap['swim_week_hours']:.1f}")
            if recap["match_count"]:
                parts.append(f"{self.t('matches_label')} {recap['match_count']}")
            if recap["best_day_name"]:
                parts.append(recap["best_day_name"])
            blocks.insert(0, soft_block(theme, self.t("recap"), " · ".join(parts), "positive"))

        total_blocks = len(blocks)
        if not self.show_all_insights:
            blocks = blocks[:3]

        label_totals = label_totals_for_week(self.page, self.week_offset)
        label_panel = []
        named = {k: v for k, v in label_totals.items() if k and v > 0}
        if named:
            ordered = sorted(named.items(), key=lambda kv: kv[1], reverse=True)[:5]
            unlabeled = label_totals.get("", 0)
            rows_lbl = [
                data_row(theme, k, fmt_hm(v), last=False) for k, v in ordered
            ]
            if unlabeled > 0:
                rows_lbl.append(
                    data_row(theme, self.t("unlabeled"), fmt_hm(unlabeled), last=True)
                )
            else:
                rows_lbl[-1] = data_row(
                    theme, ordered[-1][0], fmt_hm(ordered[-1][1]), last=True
                )
            label_panel = [
                ft.Container(height=14),
                label_text(theme, self.t("label_breakdown"), theme["ink_45"]),
                ft.Container(height=8),
                panel(theme, rows_lbl),
            ]

        grade = self.t("good_week") if delta >= 0 else self.t("recovering")
        grade_color = theme["positive"] if delta >= 0 else theme["accent"]
        week_no = (datetime.now() + timedelta(weeks=self.week_offset)).isocalendar()[1]

        def stat_card(label, value):
            return ft.Container(
                content=ft.Column(
                    [
                        label_text(theme, label, theme["ink_45"]),
                        ft.Text(value, size=21, weight=ft.FontWeight.W_500, color=theme["ink"]),
                    ],
                    spacing=4,
                    tight=True,
                ),
                bgcolor=theme["panel"],
                border_radius=16,
                padding=ft.Padding.symmetric(vertical=12, horizontal=13),
                expand=True,
            )

        return ft.Column(
            [
                ft.Row(
                    [
                        label_text(theme, self.t("report_week", n=week_no), theme["accent"]),
                        ft.Container(
                            content=ft.Text(
                                grade,
                                size=10,
                                weight=ft.FontWeight.W_600,
                                color=theme["bg"] if delta >= 0 else theme["on_accent"],
                            ),
                            bgcolor=grade_color,
                            border_radius=999,
                            padding=ft.Padding.symmetric(vertical=5, horizontal=11),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(height=18),
                ft.Row(
                    [
                        big_number(theme, f"{total / 3600:.1f}", 58),
                        ft.Column(
                            [
                                label_text(theme, self.t("hours_focus"), theme["ink_45"]),
                                ft.Text(
                                    f"{delta / 3600:+.1f}",
                                    size=13,
                                    weight=ft.FontWeight.W_500,
                                    color=theme["positive"] if delta >= 0 else theme["accent"],
                                ),
                            ],
                            spacing=3,
                            horizontal_alignment=ft.CrossAxisAlignment.END,
                            tight=True,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                ft.Container(height=18),
                ft.Container(
                    content=ft.Column(
                        [
                            week_bars(theme, values, 90),
                            ft.Container(height=6),
                            ft.Row(
                                [
                                    ft.Container(
                                        content=ft.Text(
                                            lbl,
                                            size=9,
                                            color=theme["ink_45"],
                                            text_align=ft.TextAlign.CENTER,
                                        ),
                                        expand=True,
                                    )
                                    for lbl in weekday_labels_for(self.lang)
                                ],
                                spacing=6,
                            ),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    bgcolor=theme["panel"],
                    border_radius=18,
                    padding=ft.Padding.symmetric(vertical=14, horizontal=14),
                ),
                ft.Container(height=12),
                ft.Row(
                    [
                        stat_card("GYM", f"{gym_done}/4"),
                        stat_card(
                            "YÜZME" if self.lang == "tr" else "SWIM",
                            fmt_hm(self.swim_week_seconds(self.week_offset)),
                        ),
                        stat_card(
                            "EKSTRA" if self.lang == "tr" else "EXTRA",
                            str(wreport["extra_count"]),
                        ),
                    ],
                    spacing=8,
                ),
                ft.Container(height=14),
                label_text(theme, self.t("records"), theme["ink_45"]),
                ft.Container(height=8),
                panel(
                    theme,
                    [
                        data_row(
                            theme,
                            self.t("longest_streak"),
                            f"{compute_longest_focus_streak(history)} {self.t('days')}",
                        ),
                        data_row(
                            theme,
                            self.t("best_week"),
                            f"{compute_best_focus_week(history):.1f} "
                            + self.t("hours_upper").lower(),
                        ),
                        data_row(
                            theme,
                            self.t("best_gym_week"),
                            f"{compute_best_gym_week(self.page)}/4",
                        ),
                        data_row(
                            theme,
                            self.t("matches_week"),
                            str(compute_week_match_count(self.page, week_start)),
                            last=True,
                        ),
                    ],
                ),
                ft.Container(height=14),
                label_text(theme, self.t("weekly_report"), theme["ink_45"]),
                ft.Container(height=8),
                panel(
                    theme,
                    [
                        data_row(
                            theme,
                            self.t("completed"),
                            ", ".join(wreport["completed_names"]) or self.t("none_yet"),
                        ),
                        data_row(
                            theme,
                            self.t("missing_list"),
                            ", ".join(wreport["missing_names"]) or self.t("none_yet"),
                            value_color=theme["accent"] if wreport["missing_names"] else theme["positive"],
                        ),
                        data_row(theme, self.t("extra_week"), str(wreport["extra_count"]), last=True),
                    ],
                ),
                ft.Container(height=14),
                ft.Column(blocks, spacing=9),
                ft.Container(height=10) if total_blocks > 3 else ft.Container(height=0),
                quiet_button(
                    theme,
                    self.t("show_less") if self.show_all_insights else self.t("show_all"),
                    self.on_toggle_insights,
                )
                if total_blocks > 3
                else ft.Container(height=0),
            ]
            + label_panel
            + [
                ft.Container(height=14),
                ft.Row(
                    [
                        quiet_button(theme, self.t("prev_week"), lambda e: self.shift_week(-1)),
                        quiet_button(theme, self.t("next_week"), lambda e: self.shift_week(1)),
                    ],
                    spacing=8,
                ),
                ft.Container(height=8),
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def on_toggle_insights(self, e):
        self.show_all_insights = not self.show_all_insights
        self.refresh()

    def shift_week(self, delta):
        self.week_offset = min(0, self.week_offset + delta)
        self.refresh()

    # ---------------- AYARLAR ---------------------------------
    def build_settings_screen(self):
        theme = self.theme
        code = get_account_code(self.page) or "------"
        stats = compute_alltime_stats(self.page)
        goal = load_long_term_goal(self.page)
        progress = compute_long_term_goal_progress(self.page)
        first_use = get_or_set_first_use_date(self.page)
        anniversary = compute_anniversary_years(first_use)
        yearly = compute_yearly_report(self.page, self.lang)
        rem_on, rem_hour = load_reminder_settings(self.page)

        self.code_field = ft.TextField(hint_text=self.t("enter_code"), dense=True, expand=True)
        self.goal_hours_field = ft.TextField(
            hint_text=self.t("target_hours"),
            dense=True,
            expand=True,
            value=str(goal["target_hours"]) if goal else "",
        )
        self.goal_date_field = ft.TextField(
            hint_text=self.t("target_date"),
            dense=True,
            expand=True,
            value=goal["target_date"] if goal else "",
        )

        def sec(name):
            return lambda e: self.toggle_section(name)

        # --- hesap ---
        account_controls = [
            panel(
                theme,
                [
                    data_row(theme, self.t("account"), code),
                    data_row(theme, self.t("first_use"), first_use.replace("-", "."), last=True),
                ],
            ),
            ft.Container(height=8),
            ft.Text(self.t("account_help"), size=11.5, color=theme["ink_45"]),
            ft.Container(height=10),
            ft.Row(
                [
                    self.code_field,
                    primary_button(
                        theme, self.t("use_code"), self.on_use_code, expand=False, width=120
                    ),
                ],
                spacing=8,
            ),
            ft.Container(height=8),
            ft.Row(
                [
                    quiet_button(theme, self.t("sync_now"), self.on_sync_now),
                    quiet_button(theme, self.t("new_code"), self.on_new_code),
                ],
                spacing=8,
            ),
        ]

        # --- gorunum (tema + dil) ---
        appearance_controls = [
            label_text(theme, self.t("theme_label"), theme["ink_45"]),
            ft.Container(height=8),
            ft.Row(
                [
                    pill(
                        theme,
                        THEMES[k]["name"],
                        active=(self.theme_key == k),
                        on_click=lambda e, kk=k: self.set_theme(kk),
                    )
                    for k in THEMES
                ],
                spacing=6,
            ),
            ft.Container(height=16),
            label_text(theme, self.t("language"), theme["ink_45"]),
            ft.Container(height=8),
            ft.Row(
                [
                    pill(
                        theme,
                        "Türkçe",
                        active=(self.lang == "tr"),
                        on_click=lambda e: self.set_lang("tr"),
                    ),
                    pill(
                        theme,
                        "English",
                        active=(self.lang == "en"),
                        on_click=lambda e: self.set_lang("en"),
                    ),
                ],
                spacing=6,
            ),
        ]

        # --- hatirlatma ---
        reminder_controls = [
            ft.Row(
                [
                    pill(
                        theme,
                        self.t("reminder_on"),
                        active=rem_on,
                        on_click=lambda e: self.set_reminder(True, rem_hour),
                    ),
                    pill(
                        theme,
                        self.t("reminder_off"),
                        active=not rem_on,
                        on_click=lambda e: self.set_reminder(False, rem_hour),
                    ),
                ],
                spacing=6,
            ),
            ft.Container(height=10),
            label_text(theme, self.t("reminder_hour"), theme["ink_45"]),
            ft.Container(height=8),
            ft.Row(
                [
                    pill(
                        theme,
                        f"{h}:00",
                        active=(rem_hour == h),
                        on_click=lambda e, hh=h: self.set_reminder(rem_on, hh),
                    )
                    for h in [19, 20, 21, 22, 23]
                ],
                spacing=6,
            ),
            ft.Container(height=10),
            ft.Text(self.t("reminder_help"), size=11.5, color=theme["ink_45"]),
        ]

        # --- uzun vadeli hedef ---
        goal_controls = [
            ft.Row([self.goal_hours_field, self.goal_date_field], spacing=8),
            ft.Container(height=8),
            ft.Row(
                [
                    primary_button(theme, self.t("set_goal"), self.on_save_goal),
                    quiet_button(theme, self.t("delete_goal"), self.on_clear_goal),
                ],
                spacing=8,
            ),
        ]
        if progress:
            goal_controls += [
                ft.Container(height=10),
                soft_block(
                    theme,
                    self.t("longterm"),
                    self.t(
                        "longterm_progress",
                        cur=progress["current_hours"],
                        tgt=progress["target_hours"],
                        pct=progress["pct"],
                        days=progress["days_remaining"],
                    ),
                    "positive" if progress["on_track"] else "accent",
                ),
            ]

        # --- istatistikler ---
        hours_lower = self.t("hours_upper").lower()
        stats_controls = [
            label_text(theme, self.t("alltime"), theme["ink_45"]),
            ft.Container(height=8),
            panel(
                theme,
                [
                    data_row(theme, self.t("focus_label"), f"{stats['focus_hours']:.1f} {hours_lower}"),
                    data_row(theme, "Gym", str(stats["gym_sessions"])),
                    data_row(theme, self.t("swim_label"), f"{stats['swim_hours']:.1f} {hours_lower}"),
                    data_row(theme, self.t("matches_label"), str(int(stats["matches"])), last=True),
                ],
            ),
            ft.Container(height=16),
            label_text(theme, self.t("yearly", y=yearly["year"]), theme["ink_45"]),
            ft.Container(height=8),
            panel(
                theme,
                [
                    data_row(theme, self.t("focus_label"), f"{yearly['focus_hours']:.1f} {hours_lower}"),
                    data_row(theme, self.t("swim_label"), f"{yearly['swim_hours']:.1f} {hours_lower}"),
                    data_row(
                        theme,
                        self.t("gym_rate"),
                        f"{yearly['gym_completed']}/{yearly['gym_possible']}"
                        if yearly["gym_possible"]
                        else "—",
                    ),
                    data_row(
                        theme,
                        self.t("busiest_month"),
                        f"{yearly['best_month_name']} · {yearly['best_month_hours']:.1f}"
                        if yearly["best_month_name"]
                        else "—",
                    ),
                    data_row(
                        theme, self.t("matches_label"), str(int(yearly["match_count"])), last=True
                    ),
                ],
            ),
        ]

        # --- tehlikeli bolge ---
        danger_controls = [
            ft.Text(self.t("reset_day_help"), size=11.5, color=theme["ink_45"]),
            ft.Container(height=10),
            quiet_button(
                theme, self.t("reset_day_btn"), lambda e: self.on_reset_day(self.today())
            ),
        ]

        return ft.Column(
            [
                section(
                    theme,
                    self.t("account"),
                    account_controls,
                    self.open_sections.get("account", True),
                    sec("account"),
                ),
                section(
                    theme,
                    self.t("appearance"),
                    appearance_controls,
                    self.open_sections.get("appearance", False),
                    sec("appearance"),
                ),
                section(
                    theme,
                    self.t("reminder"),
                    reminder_controls,
                    self.open_sections.get("reminder", False),
                    sec("reminder"),
                ),
                section(
                    theme,
                    self.t("longterm"),
                    goal_controls,
                    self.open_sections.get("goal", False),
                    sec("goal"),
                ),
                section(
                    theme,
                    self.t("statistics"),
                    stats_controls,
                    self.open_sections.get("stats", False),
                    sec("stats"),
                ),
                section(
                    theme,
                    self.t("danger"),
                    danger_controls,
                    self.open_sections.get("danger", False),
                    sec("danger"),
                    accent=True,
                ),
                ft.Container(height=14),
                ft.Text(
                    f"{self.t('first_use')}: {first_use.replace('-', '.')}"
                    + (f" · {anniversary}. yıl" if anniversary else ""),
                    size=11,
                    color=theme["ink_45"],
                ),
                ft.Container(height=12),
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def toggle_section(self, name):
        currently_open = self.open_sections.get(name, name == "account")
        # Akordiyon davranisi: bir bolum acilirken digerleri kapansin,
        # liste gereksiz yere uzamasin.
        self.open_sections = {"account": False, "appearance": False, "reminder": False,
                               "goal": False, "stats": False, "danger": False}
        self.open_sections[name] = not currently_open
        self.refresh()

    def set_reminder(self, on, hour):
        save_reminder_settings(self.page, on, hour)
        self.refresh()

    def set_lang(self, code):
        self.lang = code
        save_lang(self.page, code)
        self.refresh()

    def set_theme(self, key):
        self.theme_key = key
        save_theme_key(self.page, key)
        self.refresh()

    def on_use_code(self, e):
        code = (self.code_field.value or "").strip()
        if len(code) != 6 or not code.isdigit():
            self.toast(self.t("invalid_code"))
            return
        set_account_code(self.page, code)
        _MEM_CACHE.clear()
        _SYNCED_FIELDS.clear()
        self.toast(self.t("code_applied"))
        self.start_cloud_sync(
            on_done=lambda found: self.toast(self.t("code_found") if found else self.t("code_missing"))
        )
        self.refresh()

    def on_new_code(self, e):
        new_code = generate_account_code()
        set_account_code(self.page, new_code)
        _MEM_CACHE.clear()
        _SYNCED_FIELDS.clear()
        # client_storage yazmasi dogrulaniyor -- bazi Flet surumlerinde bu
        # cagri sessizce basarisiz olabiliyor, bu yuzden geri okuyup
        # gerekirse bir kez daha deniyoruz. Kullaniciya SONUCU her turlu
        # acikca gosteriyoruz (once buton hicbir geri bildirim vermiyordu,
        # bu da "hicbir sey olmuyor" hissi yaratiyordu).
        saved = get_account_code(self.page)
        if saved != new_code:
            set_account_code(self.page, new_code)
            saved = get_account_code(self.page)
        if saved == new_code:
            self.toast(self.t("new_code_done", code=new_code))
        else:
            self.toast(self.t("storage_error"))
        self.refresh()

    def on_sync_now(self, e):
        _SYNCED_FIELDS.clear()
        self.toast(self.t("sync_now"))
        self.start_cloud_sync(
            on_done=lambda found: self.toast(self.t("sync_done") if found else self.t("code_missing"))
        )

    def on_save_goal(self, e):
        try:
            hours = float((self.goal_hours_field.value or "0").replace(",", "."))
        except ValueError:
            hours = 0
        date_str = (self.goal_date_field.value or "").strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return
        if hours > 0:
            save_long_term_goal(self.page, hours, date_str)
            self.toast(self.t("saved"))
        self.refresh()

    def on_clear_goal(self, e):
        clear_long_term_goal(self.page)
        self.refresh()

    # ---------------- DIYALOG YARDIMCILARI --------------------
    def show_dialog(self, dlg):
        self.dialog = dlg
        # Flet surumune gore dialog gosterme API'si degisebiliyor
        # (show_dialog / open / eski dialog property). Hepsini sirayla
        # deniyoruz ki versiyon farkindan diyalog sessizce kaybolmasin.
        try:
            self.page.show_dialog(dlg)
            return
        except Exception:
            pass
        try:
            self.page.open(dlg)
            return
        except Exception:
            pass
        try:
            self.page.dialog = dlg
            dlg.open = True
            self.page.update()
        except Exception:
            pass

    def close_dialog(self):
        dlg = getattr(self, "dialog", None)
        if dlg is None:
            return
        try:
            self.page.pop_dialog()
            return
        except Exception:
            pass
        try:
            self.page.close(dlg)
            return
        except Exception:
            pass
        try:
            dlg.open = False
            self.page.update()
        except Exception:
            pass

    def open_choice_dialog(self, title, options, on_pick):
        theme = self.theme
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(title, size=15, weight=ft.FontWeight.W_600),
            content=ft.Column(
                [
                    ft.TextButton(opt, on_click=lambda e, o=opt: on_pick(o))
                    for opt in options
                ],
                tight=True,
                spacing=2,
            ),
            actions=[ft.TextButton(self.t("close"), on_click=lambda e: self.close_dialog())],
        )
        self.show_dialog(dlg)

    # ---------------- GUN SONU HATIRLATMASI -------------------
    def maybe_show_reminder(self):
        on, hour = load_reminder_settings(self.page)
        if not on:
            return
        now = datetime.now()
        if now.hour < hour:
            return
        today = self.today()
        if reminder_already_shown(self.page, today):
            return

        theme = self.theme
        secs = self.focus_seconds_today()
        gym = self.gym_state()
        gym_done = sum(1 for g in gym if g["done"])

        lines = [
            f"{self.t('focus_label')}: {fmt_hm(secs)}",
            f"Gym: {gym_done}/4",
        ]
        if secs > 0:
            lines.append(self.t("all_clear"))

        mark_reminder_shown(self.page, today)
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(self.t("day_summary"), size=15, weight=ft.FontWeight.W_600),
            content=ft.Column(
                [ft.Text(line, size=13.5, color=theme["ink_70"]) for line in lines],
                tight=True,
                spacing=6,
            ),
            actions=[ft.ElevatedButton(self.t("ok"), on_click=lambda e: self.close_dialog())],
        )
        self.show_dialog(dlg)

    # ---------------- KILOMETRE TASLARI -----------------------
    def check_milestones(self):
        try:
            new_ones = check_new_milestones(self.page)
        except Exception:
            new_ones = []
        if new_ones:
            self.milestone_queue.extend(new_ones)
            self.show_next_milestone()

    def show_next_milestone(self):
        if not self.milestone_queue:
            return
        category, threshold = self.milestone_queue.pop(0)
        key_map = {
            "focus_hours": "milestone_focus",
            "gym_sessions": "milestone_gym",
            "swim_hours": "milestone_swim",
            "matches": "milestone_match",
            "anniversary": "anniversary_banner",
        }
        body = tr(self.lang, key_map.get(category, "milestone_focus"), n=threshold)
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [ft.Icon(ft.Icons.CELEBRATION, color=self.theme["accent"]), ft.Text(self.t("congrats"))],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            content=ft.Text(body, size=14, text_align=ft.TextAlign.CENTER),
            actions=[
                ft.ElevatedButton(self.t("awesome"), on_click=lambda e: self.on_close_milestone())
            ],
            actions_alignment=ft.MainAxisAlignment.CENTER,
        )
        self.show_dialog(dlg)

    def on_close_milestone(self):
        self.close_dialog()
        if self.milestone_queue:
            self.show_next_milestone()

    # ---------------- KURULUM ---------------------------------
    def mount(self):
        page = self.page
        # ONEMLI: self.theme_key __init__ icinde de bir kez okunuyordu, ama
        # __init__ kontrol henuz sayfaya gercekten baglanmadan calisabiliyor
        # -- yani client_storage plugin'i cihazda henuz hazir olmadan
        # cagriliyor olabilir. Hesap kodu (get_account_code) mount()
        # icinde okunuyor ve orada guvenilir calisiyordu; tema icin de
        # ayni, daha guvenilir noktada (mount, sayfa gercekten baglandiktan
        # sonra) TEKRAR okuyoruz -- kullanicinin "karanlik moda gectim ama
        # kapatip acinca beyaza donuyor" sikayetinin sebebi muhtemelen
        # buydu: __init__'teki erken okuma varsayilana ("buz") dusuyordu.
        self.theme_key = load_theme_key(page)
        theme = self.theme
        page.title = "Organizer"
        page.padding = 0
        # Papara referans alinarak: uygulama genelinde Inter fontu.
        # Font indirilemezse (agsizlik vb.) Flutter sessizce sistem
        # fontuna geri doner -- crash riski yok.
        try:
            page.fonts = {
                "Inter": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
            }
            page.theme = ft.Theme(font_family="Inter")
            page.dark_theme = ft.Theme(font_family="Inter")
        except Exception:
            pass
        page.bgcolor = theme["bg"]
        page.theme_mode = ft.ThemeMode.DARK if theme["dark"] else ft.ThemeMode.LIGHT

        if not get_account_code(page):
            set_account_code(page, generate_account_code())

        self.header = ft.Container(
            content=self.build_header(), padding=ft.Padding.only(left=20, top=14, right=8, bottom=6)
        )
        self.nav = ft.NavigationBar(
            selected_index=self.tab,
            on_change=lambda e: self.set_tab(e.control.selected_index),
            bgcolor=theme["panel"],
            indicator_color=ft.Colors.with_opacity(0.16, theme["accent"]),
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.TODAY, label=self.t("tab_today")),
                ft.NavigationBarDestination(
                    icon=ft.Icons.TIMER_OUTLINED, label=self.t("tab_focus")
                ),
                ft.NavigationBarDestination(icon=_SPORTS_TAB_ICON, label=self.t("tab_sports")),
                ft.NavigationBarDestination(
                    icon=ft.Icons.CALENDAR_MONTH, label=self.t("tab_calendar")
                ),
                ft.NavigationBarDestination(icon=ft.Icons.INSIGHTS, label=self.t("tab_report")),
            ],
        )
        self.body.content = self.build_screen()
        self.body_shift = ft.Container(
            content=self.body,
            offset=ft.Offset(0, 0),
            animate_offset=ft.Animation(220, ft.AnimationCurve.EASE_OUT_CUBIC),
            expand=True,
        )
        self.body_wrap = ft.Container(
            content=ft.GestureDetector(
                content=self.body_shift,
                on_horizontal_drag_update=self.on_swipe_update,
                on_horizontal_drag_end=self.on_swipe_end,
            ),
            padding=ft.Padding.symmetric(vertical=0, horizontal=20),
            expand=True,
        )

        page.add(ft.Column([self.header, self.body_wrap], spacing=0, expand=True))
        page.navigation_bar = self.nav
        page.update()

        self.start_cloud_sync()

        # kapaninca duran seansi geri yukle
        try:
            self.restore_run_state()
            if self.focus_running:
                self.refresh()
                self.toast(self.t("resumed"))
        except Exception:
            pass

        # gun sonu hatirlatmasi
        try:
            self.maybe_show_reminder()
        except Exception:
            pass

        # yil donumu surprizi
        try:
            years = compute_anniversary_years(get_or_set_first_use_date(page))
            if years:
                self.milestone_queue.append(("anniversary", years))
        except Exception:
            pass
        self.check_milestones()

    def start_cloud_sync(self, on_done=None):
        """Bulut verisini arka planda ceker; degisiklik varsa ekrani yeniler.
        on_done(found: bool) verilirse, hesap kodunun bulutta gercekten
        bulunup bulunmadigi bilgisiyle en sonda bir kez cagrilir."""

        def run():
            changed = False
            found_any = False
            for field_name, cache_key in CLOUD_FIELDS:
                try:
                    result = sync_field_from_cloud(self.page, field_name, cache_key)
                except Exception:
                    result = None
                if result is not None:
                    found_any = True
                    if result:
                        changed = True
            if changed:
                try:
                    self.refresh()
                except Exception:
                    pass
            if on_done is not None:
                try:
                    on_done(found_any)
                except Exception:
                    pass

        # page.run_thread, arka plan isinin dogru sayfa baglaminda calismasini
        # saglar; aksi halde bulut senkronu bitince ekran otomatik yenilenmez.
        try:
            self.page.run_thread(run)
        except Exception:
            threading.Thread(target=run, daemon=True).start()


def main(page: ft.Page):
    app = OrganizerApp(page)
    app.mount()


if __name__ == "__main__":
    ft.app(target=main)
