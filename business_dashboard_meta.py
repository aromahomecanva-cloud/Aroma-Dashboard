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

import random
import re

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


def _get_paginated(url: str, params: dict) -> list:
    """Gọi Graph API và tự động lấy hết các trang (paging.next) nếu có.

    QUAN TRỌNG: requests' resp.raise_for_status() mặc định KHÔNG in ra nội dung lỗi thật
    của Meta (body JSON có "error": {"message", "type", "code", "error_subcode", "fbtrace_id"})
    -> khi crash chỉ thấy "403 Forbidden for url: ..." mà KHÔNG biết lý do thật (hết quyền?
    token hết hạn? rate limit? level="ad" cần quyền khác level="campaign"?). Bắt riêng lỗi
    HTTP để in kèm body thật, giúp chẩn đoán được ngay từ log GitHub Actions lần sau.
    """
    all_rows = []
    next_url, next_params = url, params
    for _ in range(200):  # giới hạn an toàn, đủ cho vài năm dữ liệu theo ngày
        resp = requests.get(next_url, params=next_params, timeout=30)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            try:
                err_body = resp.json()
            except ValueError:
                err_body = resp.text[:500]
            raise requests.exceptions.HTTPError(
                f"{e} | Meta trả về: {err_body}", response=resp
            ) from e
        body = resp.json()
        all_rows.extend(body.get("data", []))
        next_link = body.get("paging", {}).get("next")
        if not next_link:
            break
        next_url, next_params = next_link, None  # next đã có sẵn full query string
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
