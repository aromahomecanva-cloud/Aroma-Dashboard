"""
Client gọi Meta Marketing API để lấy chi phí quảng cáo (ads spend).
Docs: https://developers.facebook.com/docs/marketing-api/insights
Quyền cần: ads_read, read_insights
"""

import random

import requests

from business_dashboard_config import Config


def _get_paginated(url: str, params: dict) -> list:
    """Gọi Graph API và tự động lấy hết các trang (paging.next) nếu có."""
    all_rows = []
    next_url, next_params = url, params
    for _ in range(200):  # giới hạn an toàn, đủ cho vài năm dữ liệu theo ngày
        resp = requests.get(next_url, params=next_params, timeout=30)
        resp.raise_for_status()
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
