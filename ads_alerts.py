#!/usr/bin/env python3
"""Cảnh báo Facebook Ads theo rule (ads_alert_rules.json) -> gửi Telegram.

Đọc data.json (do business_dashboard_export_json.py tạo), áp rule lên cấp AD SET / AD / CAMPAIGN.
Gửi LẠI cảnh báo ở mỗi lần chạy cho đến khi ad set/ad đó được tắt trên Meta (tự biến mất khỏi
danh sách vì chỉ xét ad set/ad ACTIVE) HOẶC được ghi vào ads_alert_ack.json (Huy đã giải thích
lý do không tắt). Không có cảnh báo nào thì KHÔNG gửi tin.
Thiếu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID thì chỉ in ra log (dry-run), không lỗi.
"""
import json, os, sys, html, datetime as dt, urllib.request, urllib.parse
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
    chunks, cur = [], ""
    for part in msg.split("\n\n"):  # Telegram giới hạn 4096 ký tự/tin; cắt theo đoạn để không vỡ thẻ HTML
        if cur and len(cur) + len(part) > 3800:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{part}" if cur else part
    chunks.append(cur)
    for c in chunks:
        body = urllib.parse.urlencode({"chat_id": chat, "text": c, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
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
    today_vn = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=7)).date().isoformat()
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

    alerts = []  # (key, text) -- các rule phụ (đang tắt)

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
    shown_w, AD_W = W, W
    items = []  # cảnh báo có cấu trúc để trình bày + để bot đọc phản hồi
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
        series = defaultdict(list)  # 7 ngày gần nhất theo ad set, để bot trả lời câu hỏi
        st7 = (d_last - dt.timedelta(days=6)).isoformat()
        for x in data.get("ads_adsets_daily", []):
            asname.setdefault(x["adset_id"], x.get("adset_name", x["adset_id"]))
            ascamp.setdefault(x["adset_id"], x.get("campaign_id"))
            if x["date"] >= st7 and (x.get("spend") or x.get("orders")):
                series[x["adset_id"]].append([x["date"], round(x.get("spend") or 0), x.get("orders") or 0, round(x.get("revenue") or 0)])
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
            if (sp >= NO and a["orders"] == 0) or (sp > NO and a["revenue"] / sp < MR):
                bad_ads[adset_of[ad_id]].append({
                    "id": str(ad_id), "name": names[ad_id], "spend": sp, "orders": a["orders"],
                    "roas": (a["revenue"] / sp) if sp else 0, "link": links.get(str(ad_id))})

        for sid in set(aset) | set(bad_ads):
            if act_sets is not None and str(sid) not in act_sets:
                continue
            if str(sid) in acked:  # đã giải thích lý do giữ ad set -> bỏ cả ad set lẫn ad bên trong
                continue
            a = aset.get(sid) or defaultdict(float)
            sp = a["spend"]
            reason = None
            if sp >= NO and a["orders"] == 0:
                reason = "0 đơn"
            elif a["orders"] > 0 and sp >= KEEP and a["revenue"] / sp < MR:
                reason = f"ROAS < {MR}".replace(".", ",")
            culprits = sorted(bad_ads.get(sid, []), key=lambda b: -b["spend"])
            if not reason and not culprits:
                continue
            items.append({
                "level": "off" if reason else "ad", "type": "adset", "id": str(sid),
                "name": asname.get(sid, sid), "campaign": cname2.get(ascamp.get(sid), ""),
                "spend": sp, "orders": a["orders"], "roas": (a["revenue"] / sp) if sp else 0,
                "reason": reason, "ads": culprits, "daily_7d": sorted(series.get(sid, []))})

    # Sắp xếp: cần tắt ad set trước, trong mỗi nhóm tiền tiêu nhiều nhất lên đầu; đánh số #1, #2...
    items.sort(key=lambda it: (it["level"] != "off", -max(it["spend"], max((b["spend"] for b in it["ads"]), default=0))))
    for i, it in enumerate(items, 1):
        it["code"] = i

    n = len(items) + len(alerts)
    print(f"{n} cảnh báo (dữ liệu đến {last}).")
    state = {"date": last, "window_days": shown_w, "ad_window_days": AD_W, "items": items,
             "other": [t for _, t in alerts], "generated_at": today_vn}
    with open("ads_alert_state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    if not n:
        return  # khung nào ổn hết thì không gửi

    msg = render(items, [t for _, t in alerts], last, shown_w, AD_W, unknown_status)
    print(msg)
    send(msg)


def money(x):
    """Gọn cho điện thoại: 803k, 1,78tr."""
    if x >= 1_000_000:
        return f"{x / 1_000_000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + "tr"
    return f"{x / 1000:.0f}k"


def h(s):
    return html.escape(str(s), quote=False)


def stats(sp, orders, roas):
    s = f"{money(sp)} · {orders:.0f} đơn"
    if orders > 0:
        s += f" · ROAS {roas:.2f}".replace(".", ",")
    return s


def render(items, other, last, w, ad_w, unknown_status):
    d = dt.date.fromisoformat(last).strftime("%d/%m")
    off = [it for it in items if it["level"] == "off"]
    adonly = [it for it in items if it["level"] == "ad"]
    namecount = defaultdict(list)
    for it in items:
        namecount[(it["name"], it["campaign"])].append(it["code"])

    out = [f"⚠️ <b>Facebook Ads cần xem</b>\nDữ liệu đến {d} · ad set xét {w} ngày, ad xét {ad_w} ngày"]
    if off:
        out.append(f"💸 {len(off)} ad set nên tắt: tiêu <b>{money(sum(i['spend'] for i in off))}</b>, "
                   f"được {sum(i['orders'] for i in off):.0f} đơn")
    if unknown_status:
        out.append("<i>(Không lấy được trạng thái từ Meta, có thể lẫn ad set đã tắt)</i>")

    def block(it):
        dup = [c for c in namecount[(it["name"], it["campaign"])] if c != it["code"]]
        lines = [f"<b>#{it['code']} {h(it['name'])}</b>" + (f"  ⚠️ trùng tên #{', #'.join(map(str, dup))}" if dup else ""),
                 f"<i>{h(it['campaign'])}</i>"]
        if it["level"] == "off":
            lines.append(f"{stats(it['spend'], it['orders'], it['roas'])}  → <b>{h(it['reason'])}</b>")
        else:
            lines.append(f"Ad set còn ổn: {stats(it['spend'], it['orders'], it['roas'])}")
        for b in it["ads"]:
            nm = f'<a href="{h(b["link"])}">{h(b["name"])}</a>' if b.get("link") else h(b["name"])
            lines.append(f"  ↳ Ad {nm}: {stats(b['spend'], b['orders'], b['roas'])}")
        return "\n".join(lines)

    if off:
        out.append("🔴 <b>TẮT AD SET</b>\n\n" + "\n\n".join(block(i) for i in off))
    if adonly:
        out.append(f"🟡 <b>CHỈ TẮT AD</b> (số {ad_w} ngày)\n\n" + "\n\n".join(block(i) for i in adonly))
    if other:
        out.append("📌 <b>Khác</b>\n" + "\n".join(h(t) for t in other))
    out.append("💬 <i>Trả lời tin này, VD: \"giữ #3 vì đang test tới 5/11\", \"đã tắt 1 2\", hoặc hỏi gì cũng được.</i>")
    return "\n\n".join(out)


if __name__ == "__main__":
    main()
