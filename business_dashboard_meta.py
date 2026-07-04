"""
Client gọi Meta Marketing API để lấy chi phí quảng cáo (ads spend).
Docs: https://developers.facebook.com/docs/marketing-api/insights
Quyền cần: ads_read, read_insights

VÒNG MỚI - kết nối chi phí Meta Ads ở 3 CẤP ĐỘ (campaign / ad set / ads), theo yêu cầu user:
  - get_ads_detail(): gọi API 1 LẦN ở level="ad" (chứa đủ tên/id campaign + adset + ad),
    sau đó tự ROLL UP trong Python thành 3 danh sách (campaigns, adsets, ads) — vừa tiết
    kiệm rate limit Meta API, vừa đảm bảo số liệu nhất quán giữa các cấp (cộng dồn từ cùng
    1 nguồn dữ liệu thay vì gọi 3 API riêng có thể lệch nhau do timing).
  - CHANNEL (facebook/instagram) suy ra từ TÊN CAMPAIGN: chứa "instagram" (không phân biệt
    hoa/thường) HOẶC có từ "IG" đứng riêng (không dính liền chữ khác) -> Instagram, còn lại
    -> Facebook. Đây là quy ước do user cung cấp (Meta Ads chỉ chạy cho Facebook/Instagram,
    Shopee/TikTok Shop dùng nền tảng ads riêng của họ nên không cần tách ở đây).
  - get_ads_spend_daily_by_channel(): giống get_ads_spend_daily() nhưng tách riêng theo NGÀY
    x KÊNH (facebook/instagram), dùng level="campaign" + time_increment=1 để vừa có ngày vừa
    suy ra được kênh từ tên campaign.
  - "results" trong get_ads_detail(): cộng dồn TẠM THỜI toàn bộ giá trị trong field "actions"
    Meta trả về (có thể gồm nhiều loại: link_click, page_engagement, purchase...) — số liệu
    "kết quả" thật sự dùng để tính CPA sẽ cần điều chỉnh lại loại action cụ thể khi user set
    rule (xem business_dashboard_ads_rules.py).
"""

import datetime as dt
import gzip
import json
import random
import re
import time
from pathlib import Path

import requests

from business_dashboard_config import Config

_IG_NAME_RE = re.compile(r"instagram", re.IGNORECASE)
_IG_WORD_RE = re.compile(r"(?<![a-zA-Z0-9])ig(?![a-zA-Z0-9])", re.IGNORECASE)


def _infer_channel(campaign_name) -> str:
    """Suy ra kênh (facebook/instagram) từ tên campaign — xem docstring module."""
    name = campaign_name or ""
    if _IG_NAME_RE.search(name) or _IG_WORD_RE.search(name):
        return "instagram"
    return "facebook"


def _rollup(ads: list, group_keys: list) -> list:
    """Cộng dồn list các dòng 'ads' (mức chi tiết nhất) lại theo group_keys (VD theo
    campaign_id, hoặc theo adset_id) — tính lại ctr/cpc SAU KHI đã cộng dồn (không cộng
    trực tiếp ctr/cpc của từng ad vì đó là tỷ lệ, cộng thẳng sẽ sai)."""
    groups = {}
    for a in ads:
        key = tuple(a.get(k) for k in group_keys)
        g = groups.setdefault(key, {k: a.get(k) for k in group_keys})
        g["spend"] = g.get("spend", 0.0) + a.get("spend", 0.0)
        g["impressions"] = g.get("impressions", 0) + a.get("impressions", 0)
        g["clicks"] = g.get("clicks", 0) + a.get("clicks", 0)
        g["results"] = g.get("results", 0.0) + a.get("results", 0.0)
    out = []
    for g in groups.values():
        g["ctr"] = round(g["clicks"] / g["impressions"] * 100, 2) if g["impressions"] else 0.0
        g["cpc"] = round(g["spend"] / g["clicks"], 2) if g["clicks"] else 0.0
        g["cpa"] = round(g["spend"] / g["results"], 2) if g["results"] else None
        out.append(g)
    return out


# Mã lỗi Meta hay dùng cho rate limit / lỗi TẠM THỜI (nên thử lại), theo docs Meta:
# https://developers.facebook.com/docs/marketing-api/error-reference — code 4 = "Application
# request limit reached" (app-level, KHÁC với hết quyền/token sai), 17 = user request limit,
# 32 = page rate limit, 613 = custom rate limit. Meta cũng tự đánh dấu "is_transient": true
# trong body lỗi khi đây là lỗi NÊN thử lại (không phải lỗi cấu hình/quyền vĩnh viễn).
_RETRYABLE_META_CODES = {4, 17, 32, 613}


def _get_paginated(url: str, params: dict, max_retries: int = 4) -> list:
    """Gọi Graph API và tự động lấy hết các trang (paging.next) nếu có.

    QUAN TRỌNG: requests' resp.raise_for_status() mặc định KHÔNG in ra nội dung lỗi thật
    của Meta (body JSON có "error": {"message", "type", "code", "error_subcode", "fbtrace_id"})
    -> khi crash chỉ thấy "403 Forbidden for url: ..." mà KHÔNG biết lý do thật (hết quyền?
    token hết hạn? rate limit? level="ad" cần quyền khác level="campaign"?). Bắt riêng lỗi
    HTTP để in kèm body thật, giúp chẩn đoán được ngay từ log GitHub Actions lần sau.

    ĐÃ XÁC NHẬN qua log GitHub Actions thật: lỗi 403 khi gọi level="ad" (nhiều trang, tài
    khoản có nhiều ads) là "Application request limit reached" (code 4, is_transient=True)
    — TỨC LÀ RATE LIMIT của Meta theo APP, không phải thiếu quyền. Đây là lỗi TẠM THỜI, tự
    hết sau một lúc -> tự động chờ (backoff tăng dần) rồi thử lại vài lần trước khi bỏ cuộc,
    thay vì bỏ cuộc ngay ở lần lỗi đầu tiên.
    """
    all_rows = []
    next_url, next_params = url, params
    retries_left = max_retries
    for _ in range(200):  # giới hạn an toàn, đủ cho vài năm dữ liệu theo ngày
        try:
            resp = requests.get(next_url, params=next_params, timeout=90)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            # Lỗi MẠNG (timeout/mất kết nối) — cũng là lỗi TẠM THỜI, xử lý giống rate limit:
            # chờ rồi thử lại CHÍNH trang này, không bỏ cuộc ngay. Đã gặp thực tế: full pull
            # theo NGÀY x ad (level="ad" + time_increment=1, xem get_ads_detail_cached) nặng
            # hơn hẳn bản lifetime cũ -> timeout 30s trước đây quá ngắn, đã tăng lên 90s.
            if retries_left > 0:
                wait_s = 30 * (max_retries - retries_left + 1)
                print(f"[Lỗi mạng tạm thời - chờ {wait_s}s rồi thử lại ({retries_left} lượt còn lại)] {e}")
                time.sleep(wait_s)
                retries_left -= 1
                continue
            raise
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            try:
                err_body = resp.json()
            except ValueError:
                err_body = resp.text[:500]
            meta_err = err_body.get("error", {}) if isinstance(err_body, dict) else {}
            is_retryable = meta_err.get("is_transient") or meta_err.get("code") in _RETRYABLE_META_CODES
            if is_retryable and retries_left > 0:
                wait_s = 30 * (max_retries - retries_left + 1)  # 30s, 60s, 90s, 120s
                print(f"[Meta rate limit - lỗi tạm thời, chờ {wait_s}s rồi thử lại "
                      f"({retries_left} lượt còn lại)] {meta_err.get('message')}")
                time.sleep(wait_s)
                retries_left -= 1
                continue  # thử lại CHÍNH trang này (không đổi next_url/next_params)
            raise requests.exceptions.HTTPError(
                f"{e} | Meta trả về: {err_body}", response=resp
            ) from e
        body = resp.json()
        all_rows.extend(body.get("data", []))
        next_link = body.get("paging", {}).get("next")
        if not next_link:
            break
        next_url, next_params = next_link, None  # next đã có sẵn full query string
        retries_left = max_retries  # reset số lượt thử lại khi đã sang trang mới thành công
    return all_rows


def get_ads_spend(days: int | None = None) -> dict:
    """
    Trả về {"total_spend": float, "campaigns": [...]}. days=None -> lấy TOÀN BỘ lịch sử
    có thể (date_preset="maximum", Meta thường giữ tối đa ~37 tháng dữ liệu insights).
    """
    if Config.DEMO_MODE:
        return _demo_ads_spend()

    url = f"https://graph.facebook.com/{Config.META_API_VERSION}/{Config.META_AD_ACCOUNT_ID}/insights"
    params = {
        "level": "campaign",
        "fields": "campaign_name,spend,impressions,clicks,ctr,cpc",
        "date_preset": "maximum" if days is None else f"last_{days}d",
        "access_token": Config.META_ACCESS_TOKEN,
    }
    data = _get_paginated(url, params)

    campaigns = []
    total_spend = 0.0
    for row in data:
        spend = float(row.get("spend", 0))
        total_spend += spend
        campaigns.append({
            "name": row.get("campaign_name"),
            "spend": spend,
            "impressions": int(row.get("impressions", 0)),
            "clicks": int(row.get("clicks", 0)),
            "ctr": float(row.get("ctr", 0)),
            "cpc": float(row.get("cpc", 0)),
        })
    return {"total_spend": total_spend, "campaigns": campaigns}


def get_ads_spend_daily(days: int | None = None) -> list:
    """
    Trả về [{"date": "YYYY-MM-DD", "spend": float}] theo từng ngày.
    days=None -> lấy TOÀN BỘ lịch sử có thể (date_preset="maximum").
    """
    if Config.DEMO_MODE:
        return _demo_ads_spend_daily(days or 90)

    url = f"https://graph.facebook.com/{Config.META_API_VERSION}/{Config.META_AD_ACCOUNT_ID}/insights"
    params = {
        "level": "account",
        "fields": "spend",
        "time_increment": 1,
        "access_token": Config.META_ACCESS_TOKEN,
    }
    if days is None:
        params["date_preset"] = "maximum"
    else:
        import datetime as dt
        since = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
        until = dt.datetime.now().strftime("%Y-%m-%d")
        params["time_range"] = f'{{"since":"{since}","until":"{until}"}}'

    data = _get_paginated(url, params)
    return [{"date": row.get("date_start"), "spend": float(row.get("spend", 0))} for row in data]


def get_ads_detail(days: int | None = None) -> dict:
    """
    Lấy chi tiết ads ở CẢ 3 CẤP (campaign / ad set / ad) — xem docstring module để biết
    cách roll-up + suy ra channel. Trả về {"ads": [...], "adsets": [...], "campaigns": [...]}.
    """
    if Config.DEMO_MODE:
        return _demo_ads_detail()

    url = f"https://graph.facebook.com/{Config.META_API_VERSION}/{Config.META_AD_ACCOUNT_ID}/insights"
    params = {
        "level": "ad",
        "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,"
                  "spend,impressions,clicks,ctr,cpc,actions",
        "date_preset": "maximum" if days is None else f"last_{days}d",
        "access_token": Config.META_ACCESS_TOKEN,
    }
    data = _get_paginated(url, params)

    ads = []
    for row in data:
        spend = float(row.get("spend", 0))
        results = 0.0
        for a in (row.get("actions") or []):
            try:
                results += float(a.get("value", 0))
            except (TypeError, ValueError):
                pass
        campaign_name = row.get("campaign_name")
        ads.append({
            "ad_id": row.get("ad_id"), "ad_name": row.get("ad_name"),
            "adset_id": row.get("adset_id"), "adset_name": row.get("adset_name"),
            "campaign_id": row.get("campaign_id"), "campaign_name": campaign_name,
            "channel": _infer_channel(campaign_name),
            "spend": spend,
            "impressions": int(row.get("impressions", 0)),
            "clicks": int(row.get("clicks", 0)),
            "ctr": float(row.get("ctr", 0)),
            "cpc": float(row.get("cpc", 0)),
            "results": results,
            "cpa": round(spend / results, 2) if results else None,
            "actions_raw": row.get("actions") or [],
        })

    campaigns = _rollup(ads, ["campaign_id", "campaign_name", "channel"])
    adsets = _rollup(ads, ["adset_id", "adset_name", "campaign_id", "campaign_name", "channel"])

    return {"ads": ads, "adsets": adsets, "campaigns": campaigns}


def get_ads_spend_daily_by_channel(days: int | None = None) -> list:
    """
    Giống get_ads_spend_daily() nhưng tách riêng theo NGÀY x KÊNH (facebook/instagram) —
    dùng level="campaign" + time_increment=1 để vừa có ngày vừa suy ra kênh từ tên campaign.
    Trả về [{"date": "YYYY-MM-DD", "channel": "facebook"|"instagram", "spend": float}].
    """
    if Config.DEMO_MODE:
        return _demo_ads_spend_daily_by_channel(days or 90)

    url = f"https://graph.facebook.com/{Config.META_API_VERSION}/{Config.META_AD_ACCOUNT_ID}/insights"
    params = {
        "level": "campaign",
        "fields": "campaign_name,spend",
        "time_increment": 1,
        "access_token": Config.META_ACCESS_TOKEN,
    }
    if days is None:
        params["date_preset"] = "maximum"
    else:
        import datetime as dt
        since = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
        until = dt.datetime.now().strftime("%Y-%m-%d")
        params["time_range"] = f'{{"since":"{since}","until":"{until}"}}'

    data = _get_paginated(url, params)

    by_date_channel = {}
    for row in data:
        date = row.get("date_start")
        channel = _infer_channel(row.get("campaign_name"))
        spend = float(row.get("spend", 0))
        key = (date, channel)
        by_date_channel[key] = by_date_channel.get(key, 0.0) + spend

    return [{"date": d, "channel": ch, "spend": round(s, 2)} for (d, ch), s in by_date_channel.items()]


# ---------------------------------------------------------------------------
# CACHE — tránh phải kéo lại TOÀN BỘ lịch sử Meta Ads mỗi lần workflow chạy (mỗi 3 tiếng).
# Theo đề xuất của user: lần đầu kéo "maximum" và lưu lại, các lần sau chỉ kéo vài ngày gần
# nhất rồi GHI ĐÈ lên đúng những ngày đó (số liệu cũ hơn giữ nguyên). Điều này còn giúp
# TRÁNH rate limit "Application request limit reached" đã gặp phải (xem _get_paginated) vì
# hầu hết các lần chạy giờ chỉ cần vài trang thay vì kéo lại toàn bộ nhiều năm dữ liệu.
#
# LƯU Ý về get_ads_detail() cũ: hàm đó lấy TỔNG CỘNG (lifetime) mỗi ad qua toàn bộ
# date_preset="maximum", KHÔNG tách theo ngày -> không thể cache kiểu "ghi đè 3 ngày gần
# nhất" một cách an toàn (sẽ bị đè mất số liệu các ngày cũ, hoặc cộng trùng nếu ghi đè sai
# cách). Vì vậy bản CÓ CACHE (get_ads_detail_cached) đổi sang lấy dữ liệu THEO NGÀY x ad
# (time_increment=1), cache theo key (ad_id, date), rồi CỘNG DỒN lại thành lifetime totals
# ở bước cuối — đầu ra (ads/adsets/campaigns) vẫn giữ NGUYÊN FORMAT như get_ads_detail() cũ,
# nên các phần dùng nó (build_summary, dashboard...) không cần đổi gì.
# ---------------------------------------------------------------------------

def _load_cache(cache_path) -> dict:
    """Đọc cache JSON NÉN GZIP (xem _save_cache — dùng gzip vì cache_sapo_orders.json.gz bên
    module Sapo đã lên tới ~193MB không nén, vượt giới hạn 100MB/file của GitHub; áp dụng
    đồng bộ gzip cho mọi file cache_meta_*.json.gz để nhất quán và an toàn khi lớn dần)."""
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return {}
    try:
        with gzip.open(cache_path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, gzip.BadGzipFile, json.JSONDecodeError):
        return {}


def _save_cache(cache_path, cache: dict) -> None:
    raw = json.dumps(cache, ensure_ascii=False).encode("utf-8")
    with gzip.open(Path(cache_path), "wb", compresslevel=6) as f:
        f.write(raw)


def get_ads_spend_daily_cached(cache_path, incremental_days: int = 3) -> list:
    """Bản CÓ CACHE của get_ads_spend_daily() — cache theo NGÀY, mặc định chỉ kéo lại
    `incremental_days` ngày gần nhất mỗi lần chạy (Meta vẫn có thể điều chỉnh spend của vài
    ngày gần đây do attribution window, nên KHÔNG dùng số ngày quá nhỏ như 1)."""
    if Config.DEMO_MODE:
        return _demo_ads_spend_daily(90)

    cache = _load_cache(cache_path)
    by_date = cache.get("by_date", {})

    if not by_date:
        print("[Meta ads_spend_daily cache] Không có cache -> kéo TOÀN BỘ lịch sử.")
        fresh = get_ads_spend_daily(days=None)
    else:
        print(f"[Meta ads_spend_daily cache] Đã có {len(by_date)} ngày trong cache -> "
              f"chỉ kéo {incremental_days} ngày gần nhất.")
        fresh = get_ads_spend_daily(days=incremental_days)

    for row in fresh:
        if row.get("date"):
            by_date[row["date"]] = row["spend"]

    cache["by_date"] = by_date
    cache["last_updated_at"] = dt.datetime.now().isoformat()
    _save_cache(cache_path, cache)

    return [{"date": d, "spend": s} for d, s in sorted(by_date.items())]


def get_ads_spend_daily_by_channel_cached(cache_path, incremental_days: int = 3) -> list:
    """Bản CÓ CACHE của get_ads_spend_daily_by_channel() — cache theo (ngày, kênh)."""
    if Config.DEMO_MODE:
        return _demo_ads_spend_daily_by_channel(90)

    cache = _load_cache(cache_path)
    by_key = cache.get("by_date_channel", {})

    if not by_key:
        print("[Meta ads_daily_by_channel cache] Không có cache -> kéo TOÀN BỘ lịch sử.")
        fresh = get_ads_spend_daily_by_channel(days=None)
    else:
        print(f"[Meta ads_daily_by_channel cache] Đã có {len(by_key)} (ngày,kênh) trong cache -> "
              f"chỉ kéo {incremental_days} ngày gần nhất.")
        fresh = get_ads_spend_daily_by_channel(days=incremental_days)

    for row in fresh:
        key = f"{row['date']}|{row['channel']}"
        by_key[key] = {"date": row["date"], "channel": row["channel"], "spend": row["spend"]}

    cache["by_date_channel"] = by_key
    cache["last_updated_at"] = dt.datetime.now().isoformat()
    _save_cache(cache_path, cache)

    return list(by_key.values())


def _get_ads_detail_rows_range(since: str | None, until: str | None) -> list:
    """Gọi Graph API level="ad" + time_increment=1 (MỖI DÒNG = 1 ad x 1 ngày). since/until=None
    -> lấy TOÀN BỘ lịch sử (date_preset="maximum"); ngược lại lấy đúng khoảng [since, until].
    Trả về list dòng THÔ (chưa gộp lifetime) — dùng để CACHE theo (ad_id, date)."""
    url = f"https://graph.facebook.com/{Config.META_API_VERSION}/{Config.META_AD_ACCOUNT_ID}/insights"
    params = {
        "level": "ad",
        "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,"
                  "spend,impressions,clicks,actions",
        "time_increment": 1,
        "access_token": Config.META_ACCESS_TOKEN,
    }
    if since is None and until is None:
        params["date_preset"] = "maximum"
    else:
        params["time_range"] = f'{{"since":"{since}","until":"{until}"}}'
    data = _get_paginated(url, params)

    rows = []
    for row in data:
        spend = float(row.get("spend", 0))
        results = 0.0
        for a in (row.get("actions") or []):
            try:
                results += float(a.get("value", 0))
            except (TypeError, ValueError):
                pass
        campaign_name = row.get("campaign_name")
        rows.append({
            "ad_id": row.get("ad_id"), "ad_name": row.get("ad_name"),
            "adset_id": row.get("adset_id"), "adset_name": row.get("adset_name"),
            "campaign_id": row.get("campaign_id"), "campaign_name": campaign_name,
            "channel": _infer_channel(campaign_name),
            "date": row.get("date_start"),
            "spend": spend,
            "impressions": int(row.get("impressions", 0)),
            "clicks": int(row.get("clicks", 0)),
            "results": results,
        })
    return rows


def _rows_to_ads(rows: list) -> list:
    """Gộp các dòng (ad_id, date) lại thành 1 dòng LIFETIME cho mỗi ad_id (cộng dồn spend/
    impressions/clicks/results qua MỌI ngày có trong cache), tính lại ctr/cpc/cpa SAU KHI cộng.
    Output CÙNG FORMAT với get_ads_detail() cũ (không cần đổi gì ở nơi dùng nó)."""
    by_ad = {}
    for r in rows:
        aid = r.get("ad_id")
        g = by_ad.setdefault(aid, {
            "ad_id": aid, "ad_name": r.get("ad_name"),
            "adset_id": r.get("adset_id"), "adset_name": r.get("adset_name"),
            "campaign_id": r.get("campaign_id"), "campaign_name": r.get("campaign_name"),
            "channel": r.get("channel"),
            "spend": 0.0, "impressions": 0, "clicks": 0, "results": 0.0,
        })
        g["spend"] += r.get("spend", 0.0)
        g["impressions"] += r.get("impressions", 0)
        g["clicks"] += r.get("clicks", 0)
        g["results"] += r.get("results", 0.0)
        if r.get("ad_name"):  # ưu tiên tên mới nhất nếu ad có đổi tên
            g["ad_name"] = r.get("ad_name")
            g["campaign_name"] = r.get("campaign_name")
            g["channel"] = r.get("channel")
    out = []
    for g in by_ad.values():
        g["ctr"] = round(g["clicks"] / g["impressions"] * 100, 2) if g["impressions"] else 0.0
        g["cpc"] = round(g["spend"] / g["clicks"], 2) if g["clicks"] else 0.0
        g["cpa"] = round(g["spend"] / g["results"], 2) if g["results"] else None
        g["actions_raw"] = []  # đã cộng vào "results", không giữ actions_raw thô ở mức lifetime
        out.append(g)
    return out


def get_ads_detail_cached(cache_path, incremental_days: int = 3) -> dict:
    """
    Bản CÓ CACHE của get_ads_detail() — xem ghi chú lớn ở đầu phần CACHE. Cache lưu THEO NGÀY
    (ad_id, date) trong file cache_path, mặc định mỗi lần chạy chỉ kéo lại `incremental_days`
    ngày gần nhất rồi GHI ĐÈ đúng những ngày đó (ngày cũ hơn giữ nguyên từ cache).
    """
    if Config.DEMO_MODE:
        return _demo_ads_detail()

    cache = _load_cache(cache_path)
    rows_by_key = cache.get("rows", {})

    if not rows_by_key:
        print("[Meta ads_detail cache] Không có cache -> kéo TOÀN BỘ lịch sử (level=ad, theo ngày).")
        fresh_rows = _get_ads_detail_rows_range(None, None)
    else:
        since = (dt.datetime.now() - dt.timedelta(days=incremental_days)).strftime("%Y-%m-%d")
        until = dt.datetime.now().strftime("%Y-%m-%d")
        print(f"[Meta ads_detail cache] Đã có {len(rows_by_key)} dòng (ad x ngày) trong cache -> "
              f"chỉ kéo lại {incremental_days} ngày gần nhất ({since} -> {until}).")
        fresh_rows = _get_ads_detail_rows_range(since, until)

    for r in fresh_rows:
        key = f"{r.get('ad_id')}|{r.get('date')}"
        rows_by_key[key] = r

    cache["rows"] = rows_by_key
    cache["last_updated_at"] = dt.datetime.now().isoformat()
    _save_cache(cache_path, cache)

    all_rows = list(rows_by_key.values())
    ads = _rows_to_ads(all_rows)
    campaigns = _rollup(ads, ["campaign_id", "campaign_name", "channel"])
    adsets = _rollup(ads, ["adset_id", "adset_name", "campaign_id", "campaign_name", "channel"])
    print(f"[Meta ads_detail cache] Tổng: {len(rows_by_key)} dòng (ad x ngày) trong cache -> "
          f"{len(ads)} ads, {len(adsets)} ad set, {len(campaigns)} campaign.")
    return {"ads": ads, "adsets": adsets, "campaigns": campaigns}


# ---------------------------------------------------------------------------
# DEMO DATA
# ---------------------------------------------------------------------------

def _demo_ads_spend() -> dict:
    random.seed(7)
    names = ["Shopee - Video Ads", "TikTok Shop - Livestream Boost", "Shopee - Voucher Ads"]
    campaigns = []
    total = 0.0
    for n in names:
        spend = round(random.uniform(1_500_000, 6_000_000), 0)
        total += spend
        campaigns.append({
            "name": n,
            "spend": spend,
            "impressions": random.randint(50_000, 300_000),
            "clicks": random.randint(1_000, 9_000),
            "ctr": round(random.uniform(1.0, 4.5), 2),
            "cpc": round(spend / max(random.randint(1_000, 9_000), 1), 0),
        })
    return {"total_spend": total, "campaigns": campaigns}


def _demo_ads_spend_daily(days: int) -> list:
    import datetime as dt
    random.seed(11)
    now = dt.datetime.now()
    return [
        {"date": (now - dt.timedelta(days=i)).strftime("%Y-%m-%d"), "spend": round(random.uniform(300_000, 1_200_000), 0)}
        for i in range(days)
    ]


def _demo_ads_detail() -> dict:
    random.seed(13)
    campaign_names = [
        "Facebook - Video Ads Q3",
        "Instagram - Reels Boost",
        "Facebook - Livestream Sales",
        "IG Story Ads - Sản phẩm mới",
    ]
    ads = []
    for cname in campaign_names:
        cid = f"C{abs(hash(cname)) % 100000}"
        channel = _infer_channel(cname)
        for aset_i in range(2):
            asid = f"AS{abs(hash(cname + str(aset_i))) % 100000}"
            asname = f"{cname} - Ad Set {aset_i + 1}"
            for ad_i in range(3):
                adid = f"A{abs(hash(cname + str(aset_i) + str(ad_i))) % 100000}"
                spend = round(random.uniform(200_000, 2_000_000), 0)
                clicks = random.randint(50, 800)
                impressions = random.randint(5_000, 60_000)
                results = random.randint(0, 20)
                ads.append({
                    "ad_id": adid, "ad_name": f"{asname} - Ad {ad_i + 1}",
                    "adset_id": asid, "adset_name": asname,
                    "campaign_id": cid, "campaign_name": cname,
                    "channel": channel,
                    "spend": spend,
                    "impressions": impressions,
                    "clicks": clicks,
                    "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
                    "cpc": round(spend / clicks, 0) if clicks else 0.0,
                    "results": results,
                    "cpa": round(spend / results, 2) if results else None,
                    "actions_raw": [],
                })
    campaigns = _rollup(ads, ["campaign_id", "campaign_name", "channel"])
    adsets = _rollup(ads, ["adset_id", "adset_name", "campaign_id", "campaign_name", "channel"])
    return {"ads": ads, "adsets": adsets, "campaigns": campaigns}


def _demo_ads_spend_daily_by_channel(days: int) -> list:
    import datetime as dt
    random.seed(17)
    now = dt.datetime.now()
    rows = []
    for i in range(days):
        date = (now - dt.timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append({"date": date, "channel": "facebook", "spend": round(random.uniform(200_000, 800_000), 0)})
        rows.append({"date": date, "channel": "instagram", "spend": round(random.uniform(100_000, 500_000), 0)})
    return rows
