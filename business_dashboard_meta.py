"""
Client gọi Meta Marketing API để lấy chi phí quảng cáo (ads spend).
Docs: https://developers.facebook.com/docs/marketing-api/insights
Quyền cần: ads_read, read_insights
"""

import random

import requests

from business_dashboard_config import Config


def get_ads_spend(days: int = 30) -> dict:
    """
    Trả về {"total_spend": float, "campaigns": [{"name":.., "spend":.., "impressions":.., "clicks":..}]}
    """
    if Config.DEMO_MODE:
        return _demo_ads_spend()

    url = f"https://graph.facebook.com/{Config.META_API_VERSION}/{Config.META_AD_ACCOUNT_ID}/insights"
    params = {
        "level": "campaign",
        "fields": "campaign_name,spend,impressions,clicks,ctr,cpc",
        "date_preset": f"last_{days}d" if days != 30 else "last_30d",
        "access_token": Config.META_ACCESS_TOKEN,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("data", [])

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
