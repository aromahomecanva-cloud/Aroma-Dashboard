"""
Giống business_dashboard_main.py, nhưng xuất thêm data.json (dùng cho GitHub Actions
đẩy dữ liệu về, để Claude đọc và cập nhật dashboard artifact).

Lấy TOÀN BỘ lịch sử có thể (không giới hạn ngày) để dashboard lọc theo bất kỳ khoảng
thời gian nào (7 ngày / 30 ngày / theo năm...) mà không cần gọi lại API mỗi lần đổi filter.

VÒNG MỚI — CACHE + KÉO INCREMENTAL (theo đề xuất của bạn để job chạy nhanh hơn, tránh
rate limit Meta): thay vì kéo lại TOÀN BỘ lịch sử mỗi lần workflow chạy (mỗi 3 tiếng),
giờ:
  - Sapo orders: cache TOÀN BỘ vào cache_sapo_orders.json, mỗi lần chạy chỉ kéo lại 60
    ngày gần nhất rồi ghi đè (upsert theo order id) — xem get_orders_cached().
  - Meta ads (daily spend, daily theo kênh, chi tiết ad-level): cache theo NGÀY vào các
    file cache_meta_*.json, mỗi lần chạy chỉ kéo lại 3 ngày gần nhất rồi ghi đè đúng những
    ngày đó — xem get_ads_spend_daily_cached()/get_ads_spend_daily_by_channel_cached()/
    get_ads_detail_cached() trong business_dashboard_meta.py.
  - Các file cache_*.json PHẢI được commit lại vào repo (xem workflow YAML) để lần chạy
    sau đọc lại được — nếu không, mỗi lần vẫn sẽ tưởng cache rỗng và tự full-pull lại.
  - Muốn ép tải lại TOÀN BỘ (VD nghi ngờ cache lệch số) -> XÓA (các) file cache_*.json
    tương ứng trong repo rồi chạy lại; code tự nhận ra cache rỗng và tự full-pull.
"""

import json
import datetime as dt
from pathlib import Path

from business_dashboard_sapo import get_orders_cached, get_variant_sku_map
from business_dashboard_costs import load_cost_map
from business_dashboard_meta import (
    get_ads_spend, get_ads_spend_daily_cached, get_ads_detail_cached, get_ads_spend_daily_by_channel_cached,
)
from business_dashboard_settlement import load_settlement_fees, load_settlement_fee_breakdown
from business_dashboard_aggregate import build_summary, build_product_breakdown, build_daily_summary, fee_join_diagnostics
from business_dashboard_debug_fee_match import run_diagnostics as run_fee_match_diagnostics
from business_dashboard_debug_revenue import run_check as run_revenue_check
from business_dashboard_ads_rules import RULES_CONFIG, evaluate_rules, any_rule_active

BASE_DIR = Path(__file__).resolve().parent
OUT_PATH = BASE_DIR / "data.json"

# File cache — commit lại vào repo cùng data.json (xem workflow YAML). Xóa file tương ứng
# để ép full-pull lại từ đầu. Đuôi .json.gz vì cache KHÔNG nén của cửa hàng này đã lên tới
# ~193MB, vượt giới hạn 100MB/file của GitHub (bị từ chối push "GH001: Large files detected")
# -> mọi cache giờ được NÉN GZIP (xem _write_json_gz/_read_json_gz trong business_dashboard
# _sapo.py và _load_cache/_save_cache trong business_dashboard_meta.py).
SAPO_ORDERS_CACHE = BASE_DIR / "cache_sapo_orders.json.gz"
META_ADS_DAILY_CACHE = BASE_DIR / "cache_meta_ads_daily.json.gz"
META_ADS_DAILY_BY_CHANNEL_CACHE = BASE_DIR / "cache_meta_ads_daily_by_channel.json.gz"
META_ADS_DETAIL_CACHE = BASE_DIR / "cache_meta_ads_detail.json.gz"

SAPO_INCREMENTAL_DAYS = 60
META_INCREMENTAL_DAYS = 3


def main():
    # KHÔNG lọc bỏ đơn nào theo status/cancelled_on nữa (xem business_dashboard_revenue.py) —
    # đã đối chiếu thực nghiệm với báo cáo "Doanh thu theo thời gian" thật của Sapo và xác nhận
    # Sapo KHÔNG loại đơn nào khỏi báo cáo này theo status/cancelled_on. "orders_raw" == "orders"
    # dùng thẳng cho mọi hàm build_*, giữ tên orders_raw để business_dashboard_debug_revenue vẫn
    # chạy được ma trận đối chiếu như trước.
    orders_raw = get_orders_cached(SAPO_ORDERS_CACHE, incremental_days=SAPO_INCREMENTAL_DAYS)
    orders = orders_raw
    variant_sku_map = get_variant_sku_map()
    cost_map = load_cost_map()
    ads_data = get_ads_spend(days=None)  # lifetime theo campaign, ít dòng -> vẫn full-pull mỗi lần, rẻ
    ads_daily = get_ads_spend_daily_cached(META_ADS_DAILY_CACHE, incremental_days=META_INCREMENTAL_DAYS)
    settlement_df = load_settlement_fees()
    fee_breakdown_df = load_settlement_fee_breakdown()

    # MỚI: chi tiết Meta Ads ở 3 CẤP (campaign/ad set/ads) + tách theo NGÀY x KÊNH
    # (facebook/instagram) — xem business_dashboard_meta.py để biết cách suy ra channel từ
    # tên campaign, và business_dashboard_aggregate._allocate_ads_spend*() để biết cách
    # phân bổ tổng chi phí theo kênh về từng shop/page (theo tỷ lệ doanh thu).
    #
    # QUAN TRỌNG: bọc try/except RIÊNG cho phần này — nếu Meta API lỗi ở ĐÂY (rate limit,
    # token hết hạn giữa chừng phân trang...) THÌ TUYỆT ĐỐI KHÔNG được để crash toàn bộ job —
    # báo cáo doanh thu/COGS/phí (đã chạy ổn định, là phần quan trọng nhất) vẫn phải được
    # tính và commit data.json bình thường. Lỗi thật (nếu có) được lưu vào ads_detail_error
    # trong payload để xem trực tiếp trên data.json mà debug, không cần đào log Actions.
    ads_detail_error = None
    try:
        ads_detail = get_ads_detail_cached(META_ADS_DETAIL_CACHE, incremental_days=META_INCREMENTAL_DAYS)
        ads_daily_by_channel = get_ads_spend_daily_by_channel_cached(
            META_ADS_DAILY_BY_CHANNEL_CACHE, incremental_days=META_INCREMENTAL_DAYS
        )
    except Exception as e:
        ads_detail_error = str(e)
        print(f"[LỖI Meta Ads chi tiết - BỎ QUA, phần còn lại của báo cáo vẫn chạy tiếp] {ads_detail_error}")
        ads_detail = {"ads": [], "adsets": [], "campaigns": []}
        ads_daily_by_channel = []

    ads_spend_by_channel = {}
    for c in ads_detail["campaigns"]:
        ads_spend_by_channel[c["channel"]] = ads_spend_by_channel.get(c["channel"], 0.0) + c["spend"]

    # Rule cảnh báo ads/ad set (khung đã dựng sẵn, ngưỡng cụ thể user sẽ điền sau — xem
    # business_dashboard_ads_rules.py). Chỉ tính violations nếu có ít nhất 1 rule đã bật.
    ads_violations_ads = evaluate_rules(ads_detail["ads"]) if any_rule_active() else []
    ads_violations_adsets = evaluate_rules(ads_detail["adsets"]) if any_rule_active() else []

    summary = build_summary(orders, variant_sku_map, cost_map, ads_data, settlement_df, fee_breakdown_df,
                             ads_spend_by_channel=ads_spend_by_channel)
    product_breakdown = build_product_breakdown(orders, variant_sku_map, cost_map)
    daily_summary = build_daily_summary(orders, variant_sku_map, cost_map, settlement_df, fee_breakdown_df,
                                         ads_daily_by_channel=ads_daily_by_channel)

    diag = fee_join_diagnostics(orders, settlement_df)
    # Ghi kèm mẫu order["name"]/order["order_number"] thật + mẫu join_key trong file Chi phí
    # vào chính data.json (thay vì chỉ in ra log Actions, vì log Actions không lấy lại được
    # từ sandbox của Claude) -> để so sánh định dạng, chẩn đoán vì sao join có thể lệch.
    sample_order_names = sorted({str(o.get("name")) for o in orders if o.get("name")})[:15]
    sample_settlement_names = sorted(settlement_df["join_key"].astype(str).unique().tolist())[:15] \
        if not settlement_df.empty else []

    # Chẩn đoán TỰ ĐỘNG tìm field nào trong order thật khớp với "Mã chứng từ" (mã vận đơn,
    # dùng cho đơn sàn) và "Tham chiếu" dạng SON+số (dùng cho đơn ngoại sàn) — xem
    # business_dashboard_debug_fee_match.py để biết chi tiết cách làm.
    try:
        fee_match_result = run_fee_match_diagnostics(orders)
    except Exception as e:
        fee_match_result = {"error": str(e)}

    # Đối chiếu Doanh thu thuần tự tính với file "Báo cáo doanh thu theo thời gian" user đã
    # xuất trực tiếp từ Sapo (30 ngày 2026-06-01 -> 2026-06-30) — xem business_dashboard_debug_revenue.py.
    try:
        revenue_check = run_revenue_check(orders_raw)
    except Exception as e:
        revenue_check = {"error": str(e)}

    payload = {
        "updated_at": dt.datetime.now().isoformat(),
        "total_ads_spend": ads_data.get("total_spend", 0),
        "ads_note": "Ads spend giờ ĐÃ gán theo kênh facebook/instagram (suy ra từ tên campaign), "
                    "phân bổ về shop/page theo tỷ lệ doanh thu — xem ads_spend_by_channel.",
        "ads_spend_by_channel": ads_spend_by_channel,
        "channels": summary.to_dict(orient="records"),
        "product_breakdown": product_breakdown.to_dict(orient="records"),
        "daily": daily_summary.to_dict(orient="records"),
        "ads_daily": ads_daily,
        "ads_daily_by_channel": ads_daily_by_channel,
        "ads_detail": ads_detail,
        "ads_detail_error": ads_detail_error,
        "ads_rules_config": RULES_CONFIG,
        "ads_violations": {
            "ads": ads_violations_ads,
            "adsets": ads_violations_adsets,
        },
        "debug_fee_join": {
            "settlement_rows": diag["settlement_rows"],
            "matched": diag["matched"],
            "match_rate_pct": diag["match_rate"],
            "by_name": diag["by_name"],
            "by_order_number": diag["by_order_number"],
            "total_fee_from_settlement_file": float(settlement_df["total_fee"].sum()) if not settlement_df.empty else 0.0,
            "sample_order_names": sample_order_names,
            "sample_settlement_order_names": sample_settlement_names,
        },
        "debug_fee_match": fee_match_result,
        "debug_revenue_check": revenue_check,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {OUT_PATH}")
    print(f"Lấy TOÀN BỘ lịch sử — {len(orders)} đơn hàng (không lọc huỷ, xem business_dashboard_revenue.py).")
    print(f"Tổng ads spend (Meta, chưa gán kênh): {ads_data.get('total_spend', 0):,.0f}đ")
    print(f"Số SKU có giá vốn trong file: {len(cost_map)}")
    print(f"Số dòng breakdown theo ngày x sản phẩm x kênh x shop/page: {len(product_breakdown)}")
    print(f"Số dòng dữ liệu theo ngày x kênh x shop/page: {len(daily_summary)}")
    print(f"Số ngày có dữ liệu ads: {len(ads_daily)}")
    print(f"Số tổ hợp channel x shop/page nhận diện được: {len(summary)}")
    print(f"Tổng total_fee (từ file Chi phí Sapo): {settlement_df['total_fee'].sum() if not settlement_df.empty else 0:,.0f}đ")
    print(f"[Chẩn đoán join total_fee] Số dòng 'Mã chứng từ' trong file Chi phí: {diag['settlement_rows']} | "
          f"Khớp với order['name'] thật: {diag['matched']} | Tỷ lệ khớp: {diag['match_rate']}%")
    print(f"Meta Ads chi tiết: {len(ads_detail['campaigns'])} campaign, {len(ads_detail['adsets'])} ad set, "
          f"{len(ads_detail['ads'])} ads")
    print(f"Ads spend theo kênh: " + ", ".join(f"{ch}={spend:,.0f}đ" for ch, spend in ads_spend_by_channel.items()))
    if any_rule_active():
        print(f"Rule cảnh báo ads đang bật: {[k for k, v in RULES_CONFIG.items() if v is not None]} | "
              f"Số ad vi phạm: {len(ads_violations_ads)} | Số ad set vi phạm: {len(ads_violations_adsets)}")
    else:
        print("Rule cảnh báo ads: CHƯA bật rule nào (RULES_CONFIG toàn None) — xem business_dashboard_ads_rules.py.")


if __name__ == "__main__":
    main()
