#!/usr/bin/env python3
"""Cảnh báo Facebook Ads theo rule (ads_alert_rules.json) -> gửi Telegram.

Đọc data.json (do business_dashboard_export_json.py tạo), áp rule lên cấp AD SET / AD / CAMPAIGN.
Gửi LẠI cảnh báo ở mỗi lần chạy cho đến khi ad set/ad đó được tắt trên Meta (tự biến mất khỏi
danh sách vì chỉ xét ad set/ad ACTIVE) HOẶC được ghi vào ads_alert_ack.json (Huy đã giải thích
lý do không tắt). Không có cảnh báo nào thì KHÔNG gửi tin.
Thiếu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID thì chỉ in ra log (dry-run), không lỗi.
"""
import json, os, sys, datetime as dt, urllib.request, urllib.parse
from collections import defaultdict


def load(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def vnd(x):
    return f"{x:,.0f}đ".replace(",", ".")


def send(msg):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (tok and chat):
        print("(Chưa có TELEGRAM_BOT_TOKEN/CHAT_ID -> chỉ in log)")
        return False
    for i in range(0, len(msg), 3800):  # Telegram giới hạn 4096 ký tự/tin
        body = urllib.parse.urlencode({"chat_id": chat, "text": msg[i:i + 3800], "disable_web_page_preview": "true"}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=body), timeout=20)
        except Exception as e:
            print("Telegram lỗi:", e, file=sys.stderr)
            return False
    return True


def main():
    cfg = load("ads_alert_rules.json", {})
    R, W = cfg.get("rules", {}), int(cfg.get("window_days", 7))
    data = load("data.json", {})
    ads, camps = data.get("ads_ads_daily", []), data.get("ads_campaigns_daily", [])
    links = data.get("ad_post_links", {})
    if not ads:
        print("Không có ads_ads_daily, bỏ qua.")
        return
    last = max(r["date"] for r in ads)
    today_vn = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date().isoformat()
    # Dữ liệu cũ quá 2 ngày -> báo Huy pipeline dừng (thay vì cảnh báo theo số liệu đã lỗi thời)
    if (dt.date.fromisoformat(today_vn) - dt.date.fromisoformat(last)).days > 2:
        m = (f"⚠️ Dữ liệu Facebook Ads dừng ở ngày {last} (hôm nay {today_vn}) nên bot không xét được cảnh báo. "
             f"Kiểm tra workflow Update Dashboard Data trên GitHub Actions.")
        print(m)
        send(m)
        return
    d_last = dt.date.fromisoformat(last)
    start = (d_last - dt.timedelta(days=W - 1)).isoformat()

    # Ad set/ad Huy đã xác nhận "không tắt" (kèm lý do) -> không nhắc nữa, tới hết hạn (until) nếu có
    acked = set()
    for x in load("ads_alert_ack.json", {}).get("acks", []):
        if x.get("until") and x["until"] < today_vn:
            continue
        acked.add(str(x.get("id")))

    agg = defaultdict(lambda: defaultdict(float))
    names = {}
    for r in ads:
        if r["date"] < start:
            continue
        a = agg[r["ad_id"]]
        names[r["ad_id"]] = r.get("ad_name", r["ad_id"])
        for k in ("spend", "impressions", "clicks", "results", "revenue"):
            a[k] += r.get(k) or 0

    alerts = []  # (key, text)

    def add(rule, ad_id, msg):
        if str(ad_id) in acked:
            return
        link = links.get(str(ad_id))
        alerts.append((f"{last}|{rule}|{ad_id}", f"• {names[ad_id]}\n  {msg}" + (f"\n  {link}" if link else "")))

    # ---- Rule cũ theo từng AD (đang tắt trong ads_alert_rules.json; bật lại bằng enabled=true) ----
    for ad_id, a in agg.items():
        sp = a["spend"]
        r = R.get("spend_no_results", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["results"] == 0:
            add("no_result", ad_id, f"Tiêu {vnd(sp)} / {W} ngày, 0 kết quả")
        r = R.get("cost_per_result_high", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["results"] > 0 and sp / a["results"] > r["max_cpr"]:
            add("cpr", ad_id, f"CP/kết quả {vnd(sp / a['results'])} (ngưỡng {vnd(r['max_cpr'])}), tiêu {vnd(sp)}")
        r = R.get("cpm_high", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["impressions"] > 0 and sp / a["impressions"] * 1000 > r["max_cpm"]:
            add("cpm", ad_id, f"CPM {vnd(sp / a['impressions'] * 1000)} (ngưỡng {vnd(r['max_cpm'])})")
        r = R.get("cpc_high", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["clicks"] > 0 and sp / a["clicks"] > r["max_cpc"]:
            add("cpc", ad_id, f"CPC {vnd(sp / a['clicks'])} (ngưỡng {vnd(r['max_cpc'])})")
        r = R.get("roas_low", {})
        if r.get("enabled") and sp >= r["min_spend"] and a["revenue"] > 0 and a["revenue"] / sp < r["min_roas"]:
            roas = a["revenue"] / sp
            add("roas", ad_id, f"ROAS {roas:.2f} (ngưỡng {r['min_roas']}), CIR {sp / a['revenue'] * 100:.0f}%, tiêu {vnd(sp)}")

    r = R.get("spend_spike", {})
    if r.get("enabled"):
        by = defaultdict(lambda: defaultdict(float))
        cname = {}
        for c in camps:
            by[c["campaign_id"]][c["date"]] += c.get("spend") or 0
            cname[c["campaign_id"]] = c.get("campaign_name", "")
        bstart = (d_last - dt.timedelta(days=r["baseline_days"])).isoformat()
        for cid, days in by.items():
            if str(cid) in acked:
                continue
            cur = days.get(last, 0)
            base = [v for d, v in days.items() if bstart <= d < last]
            if cur >= r["min_spend"] and base:
                avg = sum(base) / r["baseline_days"]
                if avg > 0 and cur > avg * r["ratio"]:
                    alerts.append((f"{last}|spike|{cid}", f"• Campaign {cname[cid]}\n  Chi tiêu {last} {vnd(cur)} = {cur / avg:.1f}x TB {r['baseline_days']} ngày ({vnd(avg)})"))

    # ---- Rule chính: cắt lỗ theo AD SET, kèm danh sách AD gây số xấu ----
    r = R.get("adset_stoploss", {})
    unknown_status = False
    shown_w = W
    if r.get("enabled"):
        W2 = int(r.get("window_days", W))
        shown_w = W2
        st2 = (d_last - dt.timedelta(days=W2 - 1)).isoformat()
        NO, KEEP, MR = r["no_order_spend"], r["keep_until_spend"], r["min_roas"]
        AD_W = int(r.get("ad_window_days", W2))
        stA = (d_last - dt.timedelta(days=AD_W - 1)).isoformat()
        act_sets, act_ads = data.get("active_adset_ids"), data.get("active_ad_ids")
        act_sets = set(map(str, act_sets)) if act_sets is not None else None
        act_ads = set(map(str, act_ads)) if act_ads is not None else None
        unknown_status = act_sets is None or act_ads is None

        aset = defaultdict(lambda: defaultdict(float))
        asname, ascamp = {}, {}
        for x in data.get("ads_adsets_daily", []):
            asname.setdefault(x["adset_id"], x.get("adset_name", x["adset_id"]))
            ascamp.setdefault(x["adset_id"], x.get("campaign_id"))
            if x["date"] < st2:
                continue
            if act_sets is not None and str(x["adset_id"]) not in act_sets:
                continue
            a = aset[x["adset_id"]]
            for k in ("spend", "orders", "revenue"):
                a[k] += x.get(k) or 0
        cname2 = {c["campaign_id"]: c.get("campaign_name", "") for c in camps}

        ad2 = defaultdict(lambda: defaultdict(float))
        adset_of = {}
        for x in ads:
            if x["date"] < stA:
                continue
            if act_ads is not None and str(x["ad_id"]) not in act_ads:
                continue
            a = ad2[x["ad_id"]]
            names[x["ad_id"]] = x.get("ad_name", x["ad_id"])
            adset_of[x["ad_id"]] = x.get("adset_id")
            for k in ("spend", "orders", "revenue"):
                a[k] += x.get(k) or 0
        bad_ads = defaultdict(list)
        for ad_id, a in ad2.items():
            if str(ad_id) in acked:
                continue
            sp = a["spend"]
            if sp >= NO and a["orders"] == 0:
                bad_ads[adset_of[ad_id]].append((ad_id, f"tiêu {vnd(sp)}, 0 đơn"))
            elif sp > NO and a["revenue"] / sp < MR:
                bad_ads[adset_of[ad_id]].append((ad_id, f"tiêu {vnd(sp)}, {a['orders']:.0f} đơn, ROAS {a['revenue'] / sp:.2f}"))

        for sid in set(aset) | set(bad_ads):
            if act_sets is not None and str(sid) not in act_sets:
                continue
            if str(sid) in acked:  # đã giải thích lý do giữ ad set -> bỏ cả ad set lẫn ad bên trong
                continue
            a = aset.get(sid) or defaultdict(float)
            sp = a["spend"]
            verdict = None
            if str(sid) not in acked:
                if sp >= NO and a["orders"] == 0:
                    verdict = f"TẮT AD SET: tiêu {vnd(sp)} / {W2} ngày, 0 đơn"
                elif a["orders"] > 0 and sp >= KEEP and a["revenue"] / sp < MR:
                    verdict = f"TẮT AD SET: tiêu {vnd(sp)}, {a['orders']:.0f} đơn, ROAS {a['revenue'] / sp:.2f} < {MR}"
            culprits = bad_ads.get(sid, [])
            if not verdict and not culprits:
                continue
            head = (f"• Ad set {asname.get(sid, sid)} (campaign {cname2.get(ascamp.get(sid), '')})\n  "
                    + (verdict or f"Ad set chưa vượt ngưỡng (tiêu {vnd(sp)}, {a['orders']:.0f} đơn) nhưng có ad xấu"))
            lines = []
            for ad_id, why in culprits:
                link = links.get(str(ad_id))
                lines.append(f"    - Ad {names[ad_id]}: {why}" + (f"\n      {link}" if link else ""))
            if lines:
                head += f"\n  Ad gây số xấu ({AD_W} ngày, cân nhắc tắt):\n" + "\n".join(lines)
            alerts.append((f"{last}|adset|{sid}", head))

    print(f"{len(alerts)} cảnh báo (dữ liệu đến {last}).")
    if not alerts:
        return  # khung nào ổn hết thì không gửi

    body = "\n".join(t for _, t in alerts)
    note = "(Không lấy được trạng thái active từ Meta, nên danh sách có thể gồm cả ad set/ad đã tắt)\n" if unknown_status else ""
    msg = (f"⚠️ Facebook Ads cần xem ({len(alerts)} cảnh báo, dữ liệu đến {last}, cửa sổ {shown_w} ngày)\n"
           f"Tắt trên Meta hoặc nhắn Claude lý do không tắt để bot thôi nhắc.\n{note}\n{body}")
    print(msg)
    send(msg)


if __name__ == "__main__":
    main()
