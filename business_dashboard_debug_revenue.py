"""
Script CHẨN ĐOÁN: so sánh doanh thu tự tính (theo business_dashboard_revenue.py) với số liệu
THẬT lấy trực tiếp từ file "Báo cáo doanh thu theo thời gian" mà user đã xuất từ Sapo
(xuat_file_bao_cao_doanh_thu_theo_thoi_gian_04-07-2026_11-17.xls, giữ lại 30 ngày
2026-06-01 -> 2026-06-30, TOÀN SHOP không tách kênh).

VÒNG 2 (sau khi vòng 1 lệch ~7.8%, luôn UNDER-count đơn theo ngày): thử nhiều GIẢ THUYẾT
khác nhau để tìm nguyên nhân, thay vì đoán mù:
  H1 - LỆCH MÚI GIỜ: created_on trả về dạng UTC, còn Sapo tự nhóm theo ngày giờ Việt Nam
       (UTC+7) -> 1 đơn tạo lúc 23:xx VN (16:xx UTC) vẫn cùng ngày UTC, nhưng đơn tạo lúc
       00:xx-06:59 VN (17:00-23:59 UTC hôm trước) sẽ bị lệch sang ngày TRƯỚC nếu dùng UTC.
       -> thử bucket lại theo ngày SAU KHI cộng thêm 7 tiếng vào created_on.
  H2 - LỌC HUỶ SAI: có thể "cancelled_on" bị set ngay cả khi order KHÔNG thực sự bị loại
       khỏi báo cáo doanh thu của Sapo (hoặc ngược lại) -> thử nhiều tiêu chí lọc khác nhau
       (không lọc / chỉ status / chỉ cancelled_on / chỉ financial_status=="voided") và xem
       tiêu chí nào cho tổng số đơn KHỚP GẦN NHẤT với báo cáo Sapo.
Tất cả kết quả (không chỉ 1 giả thuyết) được ghi vào data.json (mục debug_revenue_check) để
Claude tự đọc qua `git clone`, so sánh và chọn ra tổ hợp đúng — theo đúng cách đã làm thành
công với việc tìm join-key cho total_fee trước đây (business_dashboard_debug_fee_match.py).
"""

import datetime as dt

from business_dashboard_revenue import order_revenue_breakdown

# Ground truth lấy trực tiếp từ file Sapo export (xem docstring trên).
GROUND_TRUTH = [
    {"date": "2026-06-30", "orders": 107, "item_revenue": 30514250.0, "discount": 6442316.0, "refund_value": 2781460.0, "net_revenue": 21290474.0, "shipping_fee": 35000.0, "total_revenue": 21325474.0},
    {"date": "2026-06-29", "orders": 65, "item_revenue": 20052000.0, "discount": 3890679.0, "refund_value": 2076980.0, "net_revenue": 14084341.0, "shipping_fee": 120000.0, "total_revenue": 14204341.0},
    {"date": "2026-06-28", "orders": 57, "item_revenue": 17517000.0, "discount": 3810862.0, "refund_value": 1428011.0, "net_revenue": 12278127.0, "shipping_fee": 120000.0, "total_revenue": 12398127.0},
    {"date": "2026-06-27", "orders": 97, "item_revenue": 34741800.0, "discount": 8231331.0, "refund_value": 3038880.0, "net_revenue": 23471589.0, "shipping_fee": 75000.0, "total_revenue": 23546589.0},
    {"date": "2026-06-26", "orders": 110, "item_revenue": 42473400.0, "discount": 8328361.0, "refund_value": 4879732.0, "net_revenue": 29265307.0, "shipping_fee": 60000.0, "total_revenue": 29325307.0},
    {"date": "2026-06-25", "orders": 124, "item_revenue": 43619600.0, "discount": 9955102.0, "refund_value": 5506100.0, "net_revenue": 28158398.0, "shipping_fee": 145000.0, "total_revenue": 28303398.0},
    {"date": "2026-06-24", "orders": 107, "item_revenue": 34413000.0, "discount": 7383802.0, "refund_value": 2035366.0, "net_revenue": 24993832.0, "shipping_fee": 70000.0, "total_revenue": 25063832.0},
    {"date": "2026-06-23", "orders": 98, "item_revenue": 35003900.0, "discount": 8158619.0, "refund_value": 3665380.0, "net_revenue": 23179901.0, "shipping_fee": 0.0, "total_revenue": 23179901.0},
    {"date": "2026-06-22", "orders": 99, "item_revenue": 36658360.0, "discount": 7458925.0, "refund_value": 1293621.0, "net_revenue": 27905814.0, "shipping_fee": 25000.0, "total_revenue": 27930814.0},
    {"date": "2026-06-21", "orders": 85, "item_revenue": 25386000.0, "discount": 5147285.0, "refund_value": 1772328.0, "net_revenue": 18466387.0, "shipping_fee": 60000.0, "total_revenue": 18526387.0},
    {"date": "2026-06-20", "orders": 92, "item_revenue": 27491000.0, "discount": 5958873.0, "refund_value": 2718116.0, "net_revenue": 18814011.0, "shipping_fee": 35000.0, "total_revenue": 18849011.0},
    {"date": "2026-06-19", "orders": 93, "item_revenue": 31463750.0, "discount": 5417623.0, "refund_value": 2828131.0, "net_revenue": 23217996.0, "shipping_fee": 30000.0, "total_revenue": 23247996.0},
    {"date": "2026-06-18", "orders": 90, "item_revenue": 28170200.0, "discount": 6319585.0, "refund_value": 3714491.0, "net_revenue": 18136124.0, "shipping_fee": 85000.0, "total_revenue": 18221124.0},
    {"date": "2026-06-17", "orders": 106, "item_revenue": 30735000.0, "discount": 5962142.0, "refund_value": 3777857.0, "net_revenue": 20995001.0, "shipping_fee": 370000.0, "total_revenue": 21365001.0},
    {"date": "2026-06-16", "orders": 105, "item_revenue": 28190250.0, "discount": 6261267.0, "refund_value": 1856260.0, "net_revenue": 20072723.0, "shipping_fee": 25000.0, "total_revenue": 20097723.0},
    {"date": "2026-06-15", "orders": 87, "item_revenue": 34196000.0, "discount": 8589233.0, "refund_value": 3995132.0, "net_revenue": 21611635.0, "shipping_fee": 150000.0, "total_revenue": 21761635.0},
    {"date": "2026-06-14", "orders": 77, "item_revenue": 26425000.0, "discount": 5128545.0, "refund_value": 2505968.0, "net_revenue": 18790487.0, "shipping_fee": 140000.0, "total_revenue": 18930487.0},
    {"date": "2026-06-13", "orders": 58, "item_revenue": 15780000.0, "discount": 3692296.0, "refund_value": 719900.0, "net_revenue": 11367804.0, "shipping_fee": 35000.0, "total_revenue": 11402804.0},
    {"date": "2026-06-12", "orders": 67, "item_revenue": 28370240.0, "discount": 5606236.0, "refund_value": 1218818.0, "net_revenue": 21545186.0, "shipping_fee": 70000.0, "total_revenue": 21615186.0},
    {"date": "2026-06-11", "orders": 83, "item_revenue": 27507000.0, "discount": 6261029.0, "refund_value": 1501049.0, "net_revenue": 19744922.0, "shipping_fee": 35000.0, "total_revenue": 19779922.0},
    {"date": "2026-06-10", "orders": 94, "item_revenue": 42570000.0, "discount": 10305606.0, "refund_value": 1288150.0, "net_revenue": 30976244.0, "shipping_fee": 110000.0, "total_revenue": 31086244.0},
    {"date": "2026-06-09", "orders": 60, "item_revenue": 25706000.0, "discount": 4681177.0, "refund_value": 7472988.0, "net_revenue": 13551835.0, "shipping_fee": 35000.0, "total_revenue": 13586835.0},
    {"date": "2026-06-08", "orders": 63, "item_revenue": 28904800.0, "discount": 6335471.0, "refund_value": 1184900.0, "net_revenue": 21384429.0, "shipping_fee": 0.0, "total_revenue": 21384429.0},
    {"date": "2026-06-07", "orders": 69, "item_revenue": 19145000.0, "discount": 4604940.0, "refund_value": 2163369.0, "net_revenue": 12376691.0, "shipping_fee": 0.0, "total_revenue": 12376691.0},
    {"date": "2026-06-06", "orders": 109, "item_revenue": 33610000.0, "discount": 8902899.0, "refund_value": 3233006.0, "net_revenue": 21474095.0, "shipping_fee": 105000.0, "total_revenue": 21579095.0},
    {"date": "2026-06-05", "orders": 66, "item_revenue": 20567000.0, "discount": 4552715.0, "refund_value": 3094874.0, "net_revenue": 12919411.0, "shipping_fee": 105000.0, "total_revenue": 13024411.0},
    {"date": "2026-06-04", "orders": 45, "item_revenue": 15743000.0, "discount": 3726472.0, "refund_value": 768112.0, "net_revenue": 11248416.0, "shipping_fee": 90000.0, "total_revenue": 11338416.0},
    {"date": "2026-06-03", "orders": 84, "item_revenue": 30457000.0, "discount": 6590515.0, "refund_value": 2944730.0, "net_revenue": 20921755.0, "shipping_fee": 120000.0, "total_revenue": 21041755.0},
    {"date": "2026-06-02", "orders": 69, "item_revenue": 21542000.0, "discount": 4941803.0, "refund_value": 1178000.0, "net_revenue": 15422197.0, "shipping_fee": 0.0, "total_revenue": 15422197.0},
    {"date": "2026-06-01", "orders": 71, "item_revenue": 21940000.0, "discount": 4443458.0, "refund_value": 1900030.0, "net_revenue": 15596512.0, "shipping_fee": 110000.0, "total_revenue": 15706512.0},
]
TRUTH_DATES = {t["date"] for t in GROUND_TRUTH}
TRUTH_ORDERS_TOTAL = sum(t["orders"] for t in GROUND_TRUTH)


def _parse_dt(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _date_utc(o: dict) -> str:
    return str(o.get("created_on") or "")[:10]


def _date_vn(o: dict) -> str:
    """Bucket theo ngày giờ Việt Nam (UTC+7) thay vì lấy thẳng 10 ký tự đầu của created_on."""
    parsed = _parse_dt(o.get("created_on"))
    if parsed is None:
        return str(o.get("created_on") or "")[:10]
    vn = parsed + dt.timedelta(hours=7)
    return vn.strftime("%Y-%m-%d")


# --- Các giả thuyết lọc đơn huỷ (H2) ---
def _filter_none(orders):
    return list(orders)


def _filter_status_cancelled(orders):
    return [o for o in orders if o.get("status") != "cancelled"]


def _filter_cancelled_on(orders):
    return [o for o in orders if not o.get("cancelled_on")]


def _filter_status_or_cancelled_on(orders):
    return [o for o in orders if o.get("status") != "cancelled" and not o.get("cancelled_on")]


def _filter_financial_voided(orders):
    return [o for o in orders if o.get("financial_status") != "voided"]


CANCEL_FILTERS = {
    "no_filter": _filter_none,
    "status_cancelled_only": _filter_status_cancelled,
    "cancelled_on_only": _filter_cancelled_on,
    "status_or_cancelled_on (hiện tại)": _filter_status_or_cancelled_on,
    "financial_status_voided_only": _filter_financial_voided,
}

DATE_FIELDS = {
    "created_on_utc (hiện tại)": _date_utc,
    "created_on_vn_+7h": _date_vn,
}


def _score_combo(orders_filtered: list, date_fn) -> dict:
    """Đếm số đơn theo ngày (theo date_fn) trong đúng 30 ngày GROUND_TRUTH, so khớp tổng số đơn."""
    counts = {}
    for o in orders_filtered:
        d = date_fn(o)
        if d in TRUTH_DATES:
            counts[d] = counts.get(d, 0) + 1
    total_computed = sum(counts.values())
    abs_diff_orders = sum(abs(counts.get(t["date"], 0) - t["orders"]) for t in GROUND_TRUTH)
    exact_day_matches = sum(1 for t in GROUND_TRUTH if counts.get(t["date"], 0) == t["orders"])
    return {
        "total_orders_in_window": total_computed,
        "truth_total_orders": TRUTH_ORDERS_TOTAL,
        "diff_total_orders": total_computed - TRUTH_ORDERS_TOTAL,
        "sum_abs_diff_per_day": abs_diff_orders,
        "exact_day_matches": exact_day_matches,
    }


def _status_distribution(orders: list) -> dict:
    dist = {}
    for o in orders:
        key = f"status={o.get('status')!r}"
        dist[key] = dist.get(key, 0) + 1
    fin_dist = {}
    for o in orders:
        key = f"financial_status={o.get('financial_status')!r}"
        fin_dist[key] = fin_dist.get(key, 0) + 1
    cancelled_on_present = sum(1 for o in orders if o.get("cancelled_on"))
    return {
        "status_distribution": dist,
        "financial_status_distribution": fin_dist,
        "orders_with_cancelled_on_set": cancelled_on_present,
    }


def run_check(orders_raw: list) -> dict:
    """
    orders_raw: TOÀN BỘ orders lấy từ Sapo (CHƯA lọc huỷ, CHƯA bucket ngày).
    Thử MA TRẬN (cancel_filter x date_field) để tìm tổ hợp khớp gần nhất với báo cáo Sapo,
    thay vì chỉ tin 1 giả thuyết duy nhất.
    """
    matrix = {}
    for filter_name, filter_fn in CANCEL_FILTERS.items():
        filtered = filter_fn(orders_raw)
        for date_name, date_fn in DATE_FIELDS.items():
            combo_key = f"{filter_name} | {date_name}"
            matrix[combo_key] = _score_combo(filtered, date_fn)

    # Tổ hợp tốt nhất = sum_abs_diff_per_day nhỏ nhất.
    best_combo = min(matrix.items(), key=lambda kv: kv[1]["sum_abs_diff_per_day"])

    # Chi tiết theo NGÀY cho tổ hợp HIỆN TẠI (status_or_cancelled_on + created_on_utc) VÀ
    # tổ hợp TỐT NHẤT tìm được, để so sánh trực quan.
    current_filtered = _filter_status_or_cancelled_on(orders_raw)
    best_filter_fn = CANCEL_FILTERS[best_combo[0].split(" | ")[0]]
    best_date_fn = DATE_FIELDS[best_combo[0].split(" | ")[1]]
    best_filtered = best_filter_fn(orders_raw)

    def _rows_for(filtered, date_fn):
        by_date = {}
        for o in filtered:
            d = date_fn(o)
            if d not in TRUTH_DATES:
                continue
            b = by_date.setdefault(d, {"orders": 0, "item_revenue": 0.0, "discount": 0.0,
                                        "refund_value": 0.0, "net_revenue": 0.0, "shipping_fee": 0.0})
            rev = order_revenue_breakdown(o)
            b["orders"] += 1
            b["item_revenue"] += rev["item_revenue"]
            b["discount"] += rev["discount"]
            b["refund_value"] += rev["refund_value"]
            b["net_revenue"] += rev["net_revenue"]
            b["shipping_fee"] += rev["shipping_fee"]
        rows = []
        total_abs_diff_net = 0.0
        for t in GROUND_TRUTH:
            c = by_date.get(t["date"], {"orders": 0, "item_revenue": 0.0, "discount": 0.0,
                                         "refund_value": 0.0, "net_revenue": 0.0, "shipping_fee": 0.0})
            diff_net = round(c["net_revenue"] - t["net_revenue"], 2)
            total_abs_diff_net += abs(diff_net)
            rows.append({
                "date": t["date"], "truth": t, "computed": {k: round(v, 2) for k, v in c.items()},
                "diff_orders": c["orders"] - t["orders"], "diff_net_revenue": diff_net,
            })
        return rows, round(total_abs_diff_net, 2)

    current_rows, current_abs_diff_net = _rows_for(current_filtered, _date_utc)
    best_rows, best_abs_diff_net = _rows_for(best_filtered, best_date_fn)

    return {
        "note": "So sánh MA TRẬN (cách lọc đơn huỷ x cách bucket ngày) để tìm tổ hợp khớp nhất với báo cáo Sapo thật.",
        "matrix": matrix,
        "best_combo": best_combo[0],
        "best_combo_score": best_combo[1],
        "status_distribution_all_orders": _status_distribution(orders_raw),
        "current_approach": {
            "label": "status_or_cancelled_on (hiện tại) | created_on_utc (hiện tại)",
            "total_abs_diff_net_revenue": current_abs_diff_net,
            "rows": current_rows,
        },
        "best_approach": {
            "label": best_combo[0],
            "total_abs_diff_net_revenue": best_abs_diff_net,
            "rows": best_rows,
        },
    }
