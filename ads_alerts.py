#!/usr/bin/env python3
"""Cảnh báo Facebook Ads theo rule (ads_alert_rules.json) -> gửi Telegram.

Đọc data.json (do business_dashboard_export_json.py tạo), áp rule lên cấp AD và CAMPAIGN,
chỉ gửi những cảnh báo CHƯA gửi trong ngày (dedupe bằng ads_alert_state.json).
Thiếu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID thì chỉ in ra log (dry-run), không lỗi.
"""
import json, os, sys, datetime as dt, urllib.request, urllib.parse
from collections import defaultdict

def load(p, default):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except Exception: return default

def vnd(x): return f"{x:,.0f}đ".replace(",", ".")

def main():
    cfg = load("ads_alert_rules.json", {})
    R, W = cfg.get("rules", {}), int(cfg.get("window_days", 3))
    data = load("data.json", {})
    ads, camps = data.get("ads_ads_daily", []), data.get("ads_campaigns_daily", [])
    links = data.get("ad_post_links", {})
    if not ads: print("Không có ads_ads_daily, bỏ qua."); return
    last = max(r["date"] for r in ads)
    today_vn = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date().isoformat()
    # Dữ liệu cũ quá 2 ngày -> không cảnh báo (tránh báo theo số liệu đã lỗi thời)
    if (dt.date.fromisoformat(today_vn) - dt.date.fromisoformat(last)).days > 2:
        print(f"Dữ liệu ads mới nhất là {last}, quá cũ so với {today_vn} -> bỏ qua."); return
    d_last = dt.date.fromisoformat(last)
    start = (d_last - dt.timedelta(days=W - 1)).isoformat()

    agg = defaultdict(lambda: defaultdict(float)); names = {}
    for r in ads:
        if r["date"] < start: continue
        a = agg[r["ad_id"]]; names[r["ad_id"]] = r.get("ad_name", r["ad_id"])
        for k in ("spend", "impressions", "clicks", "results", "revenue"): a[k] += r.get(k) or 0

    alerts = []  # (key, text)
    def add(rule, ad_id, msg):
        link = links.get(str(ad_id))
        alerts.append((f"{last}|{rule}|{ad_id}", f"• {names[ad_id]}\n  {msg}" + (f"\n  {link}" if link else "")))

    for ad_id, a in agg.items():
        sp = a["spend"]
        r = R.get("spend_no_results", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["results"] == 0:
            add("no_result", ad_id, f"Tiêu {vnd(sp)} / {W} ngày, 0 kết quả")
        r = R.get("cost_per_result_high", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["results"] > 0 and sp / a["results"] > r["max_cpr"]:
            add("cpr", ad_id, f"CP/kết quả {vnd(sp/a['results'])} (ngưỡng {vnd(r['max_cpr'])}), tiêu {vnd(sp)}")
        r = R.get("cpm_high", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["impressions"] > 0 and sp / a["impressions"] * 1000 > r["max_cpm"]:
            add("cpm", ad_id, f"CPM {vnd(sp/a['impressions']*1000)} (ngưỡng {vnd(r['max_cpm'])})")
        r = R.get("cpc_high", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["clicks"] > 0 and sp / a["clicks"] > r["max_cpc"]:
            add("cpc", ad_id, f"CPC {vnd(sp/a['clicks'])} (ngưỡng {vnd(r['max_cpc'])})")
        r = R.get("roas_low", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["revenue"] > 0 and a["revenue"] / sp < r["min_roas"]:
            roas = a["revenue"] / sp
            add("roas", ad_id, f"ROAS {roas:.2f} (ngưỡng {r['min_roas']}), CIR {sp/a['revenue']*100:.0f}%, tiêu {vnd(sp)}")

    r = R.get("spend_spike", {})
    if r.get("enabled"):
        by = defaultdict(lambda: defaultdict(float)); cname = {}
        for c in camps:
            by[c["campaign_id"]][c["date"]] += c.get("spend") or 0; cname[c["campaign_id"]] = c.get("campaign_name", "")
        bstart = (d_last - dt.timedelta(days=r["baseline_days"])).isoformat()
        for cid, days in by.items():
            cur = days.get(last, 0)
            base = [v for d, v in days.items() if bstart <= d < last]
            if cur >= r["min_spend"] and base:
                avg = sum(base) / r["baseline_days"]
                if avg > 0 and cur > avg * r["ratio"]:
                    alerts.append((f"{last}|spike|{cid}", f"• Campaign {cname[cid]}\n  Chi tiêu {last} {vnd(cur)} = {cur/avg:.1f}x TB {r['baseline_days']} ngày ({vnd(avg)})"))

    state = load("ads_alert_state.json", {"sent": []})
    sent = set(k for k in state.get("sent", []) if k.split("|")[0] >= start)  # giữ state gọn
    new = [(k, t) for k, t in alerts if k not in sent]
    print(f"{len(alerts)} cảnh báo, {len(new)} mới (dữ liệu đến {last}).")
    if not new: 
        json.dump({"sent": sorted(sent)}, open("ads_alert_state.json", "w"), indent=1); return

    msg = f"⚠️ Facebook Ads cần xem ({len(new)} cảnh báo, dữ liệu đến {last}, cửa sổ {W} ngày)\n\n" + "\n".join(t for _, t in new)
    print(msg)
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    ok = False
    if tok and chat:
        for i in range(0, len(msg), 3800):  # Telegram giới hạn 4096 ký tự/tin
            body = urllib.parse.urlencode({"chat_id": chat, "text": msg[i:i+3800], "disable_web_page_preview": "true"}).encode()
            try:
                urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=body), timeout=20); ok = True
            except Exception as e:
                print("Telegram lỗi:", e, file=sys.stderr); ok = False; break
    else:
        print("(Chưa có TELEGRAM_BOT_TOKEN/CHAT_ID -> chỉ in log, không đánh dấu đã gửi)")
    if ok: sent.update(k for k, _ in new)
    json.dump({"sent": sorted(sent)}, open("ads_alert_state.json", "w"), indent=1)

if __name__ == "__main__":
    main()
