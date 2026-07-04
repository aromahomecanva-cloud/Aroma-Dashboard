"""
Tính DOANH THU THUẦN theo ĐÚNG công thức Sapo dùng trong báo cáo "Doanh thu theo thời gian"
(Sapo Admin -> Báo cáo -> Doanh thu theo thời gian), dựa trên field chính thức của Order API
(https://support.sapo.vn/cac-thuoc-tinh-cua-order-api):

  Tiền hàng           = total_line_items_price (tổng giá trị các line item, TRƯỚC giảm giá)
  Giảm giá            = total_discounts (tổng giảm giá áp dụng cho toàn đơn)
  Tiền hàng trả lại   = tổng "subtotal" của các refund_line_items trong "refunds"
                        (ĐÃ SỬA ở vòng 3 — xem bên dưới, KHÔNG dùng price*quantity nữa)
  Doanh thu thuần     = Tiền hàng - Giảm giá - Tiền hàng trả lại
  Phí giao hàng       = tổng price của shipping_lines
  Tổng doanh thu      = Doanh thu thuần + Phí giao hàng + Tiền thuế

QUAN TRỌNG - ĐÃ ĐỐI CHIẾU VỚI SỐ THẬT (business_dashboard_debug_revenue.py, chạy ma trận
5 cách lọc huỷ x 2 cách tính ngày trên ~2537 đơn / 30 ngày, so với báo cáo Sapo thật):
  - KHÔNG lọc bỏ đơn nào theo status/cancelled_on/financial_status cả — "no_filter" khớp
    TỐT NHẤT (28/30 ngày khớp chính xác, lệch tổng chỉ 2 đơn/2537 đơn). Ban đầu tưởng cần
    loại đơn huỷ (theo lời user "nhớ trừ hết đơn hoàn hủy"), nhưng dữ liệu thật cho thấy
    trường "cancelled_on" được set trên tới 3146 đơn dù chỉ có 6 đơn thực sự status="cancelled"
    — và loại bỏ theo cancelled_on làm kết quả LỆCH NẶNG hơn (thiếu ~268 đơn/2537). Nhiều khả
    năng "loại trừ hàng hoàn/huỷ" mà user nói đã được phản ánh qua việc TRỪ "Tiền hàng trả lại"
    (refund_value) trong công thức Doanh thu thuần, chứ không phải loại bỏ nguyên cả đơn.
  - NGÀY của order phải tính theo GIỜ VIỆT NAM (UTC+7), không phải giờ UTC thô của created_on
    — xem business_dashboard_aggregate._order_date().

VÒNG 3 - SỬA CÔNG THỨC refund_value (ĐÃ XÁC NHẬN qua dump nguyên văn field "refunds" của 15
đơn thật, xem business_dashboard_debug_revenue.py._raw_refund_samples):
  - refund_line_items[].line_item.price là ĐƠN GIÁ GỐC TRƯỚC giảm giá của dòng hàng đó, KHÔNG
    phải số tiền thực sự đã hoàn -> nhân price*quantity làm CAO HƠN thực tế (không áp dụng
    discount cho phần hàng hoàn).
  - refund_line_items[].subtotal (= line_item.discounted_total) mới là số tiền THỰC SỰ hoàn
    cho dòng đó — đã đối chiếu khớp TUYỆT ĐỐI với transactions[].amount (tổng giao dịch hoàn
    tiền thật) trên toàn bộ 15 mẫu kiểm tra được.
  - refund.total_refunded ĐÁNG NGỜ: bằng 0 trên một số refund dù subtotal/transactions > 0
    (có vẻ là bug/field không đáng tin cậy của Sapo) -> KHÔNG dùng field này.
  -> refund_value = sum(refund_line_items[].subtotal), fallback về price*quantity chỉ khi
     "subtotal" không tồn tại trong dữ liệu (phòng hờ).
is_cancelled()/filter_valid_orders() vẫn giữ lại trong file này (không dùng trong pipeline
chính nữa) để phục vụ chẩn đoán/tương lai nếu cần, nhưng KHÔNG áp dụng mặc định.

Trước đây dashboard dùng trực tiếp field "total_price" (tổng tiền khách phải trả, ĐÃ gồm
phí ship/thuế, và KHÔNG trừ giá trị hàng trả lại) làm "DT gộp" -> lệch khá xa so với
"Doanh thu thuần" thật của Sapo (đã xác nhận qua file "Báo cáo doanh thu theo thời gian"
user xuất trực tiếp từ Sapo).

VÒNG 3b - NGÀY của refund_value phải là ngày CỦA CHÍNH sự kiện refund, KHÔNG phải ngày tạo
đơn (ĐÃ XÁC NHẬN qua business_dashboard_debug_revenue.py._score_refund_date_attribution: bucket
theo ngày refund [processed_at/created_on của object refund] khớp gần tuyệt đối với báo cáo
Sapo — lệch tổng chỉ 35,000đ/78.5 triệu trên 30 ngày, so với lệch 6.3 triệu nếu bucket theo
ngày tạo đơn). Một đơn có thể được TẠO ngày X nhưng HOÀN TIỀN vào ngày Y khác. Hàm refund_events()
bên dưới trả về từng sự kiện refund kèm ngày riêng của nó, để business_dashboard_aggregate.py
gắn "Tiền hàng trả lại" vào đúng ngày xảy ra refund thay vì dồn hết vào ngày tạo đơn.
"""

import datetime as dt


def is_cancelled(o: dict) -> bool:
    """Đơn bị huỷ: status == 'cancelled' HOẶC có cancelled_on (khác null/rỗng)."""
    if o.get("status") == "cancelled":
        return True
    if o.get("cancelled_on"):
        return True
    return False


def filter_valid_orders(orders: list) -> list:
    """
    KHÔNG dùng trong pipeline chính (xem docstring module) — đã xác nhận thực nghiệm rằng
    Sapo KHÔNG loại bỏ đơn theo status/cancelled_on trong báo cáo "Doanh thu theo thời gian".
    Giữ lại hàm này chỉ để tham khảo/chẩn đoán nếu cần điều tra lại sau này.
    """
    return [o for o in orders if not is_cancelled(o)]


def _to_float(v) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_iso_dt(s):
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _vn_date_str(parsed_dt):
    if parsed_dt is None:
        return None
    return (parsed_dt + dt.timedelta(hours=7)).strftime("%Y-%m-%d")


def refund_events(o: dict) -> list:
    """
    Trả về list các (refund_date_vn, amount) — 1 phần tử cho MỖI object refund trong order
    (gộp các refund_line_items bên trong 1 refund lại thành 1 số tiền), amount = tổng
    "subtotal" của các refund_line_items (số tiền THỰC SỰ hoàn, đã trừ giảm giá — xem docstring
    module), refund_date_vn = ngày (giờ VN, UTC+7) của CHÍNH sự kiện refund (lấy processed_at,
    fallback created_on của object refund) — KHÔNG phải ngày tạo đơn.

    ĐÃ XÁC NHẬN: Sapo tính "Tiền hàng trả lại" theo NGÀY REFUND xảy ra, không phải ngày đơn
    được tạo — xem docstring module. Nếu không parse được ngày refund, trả về (None, amount);
    caller nên fallback về ngày tạo đơn trong trường hợp hiếm gặp này.
    """
    events = []
    for rf in (o.get("refunds") or []):
        amt = 0.0
        for rli in (rf.get("refund_line_items") or []):
            if rli.get("subtotal") is not None:
                amt += _to_float(rli.get("subtotal"))
            else:
                li = rli.get("line_item") or {}
                amt += _to_float(li.get("price")) * _to_float(rli.get("quantity"))
        if amt == 0.0:
            continue
        date_vn = None
        for key in ("processed_at", "created_on"):
            parsed = _parse_iso_dt(rf.get(key))
            if parsed is not None:
                date_vn = _vn_date_str(parsed)
                break
        events.append((date_vn, amt))
    return events


def order_revenue_breakdown(o: dict) -> dict:
    """
    Trả về dict các thành phần doanh thu của 1 order, theo đúng công thức báo cáo Sapo
    "Doanh thu theo thời gian" (xem docstring module):
      item_revenue, discount, refund_value, shipping_fee, tax, net_revenue, total_revenue

    LƯU Ý: refund_value ở đây là TỔNG (không phân biệt ngày) — dùng cho tổng theo channel/
    shop_page (build_summary, không tách theo ngày, không bị ảnh hưởng bởi vấn đề "ngày nào").
    Khi cần tách theo NGÀY (build_daily_summary), dùng refund_events() ở trên để gắn đúng
    ngày của từng sự kiện refund, không dùng số tổng ở đây.
    """
    item_revenue = _to_float(o.get("total_line_items_price"))
    discount = _to_float(o.get("total_discounts"))

    refund_value = sum(amt for _, amt in refund_events(o))

    shipping_fee = sum(_to_float(sl.get("price")) for sl in (o.get("shipping_lines") or []))

    # Sapo Order API (theo tài liệu chính thức) không thấy field thuế cấp order rõ ràng
    # (không có total_tax) -> để 0 (thực tế cột "Tiền thuế" trong báo cáo Sapo cũng luôn = 0
    # đối với dữ liệu của shop này, xem file "Báo cáo doanh thu theo thời gian" đã đối chiếu).
    tax = 0.0

    net_revenue = item_revenue - discount - refund_value
    total_revenue = net_revenue + shipping_fee + tax

    return {
        "item_revenue": item_revenue,
        "discount": discount,
        "refund_value": refund_value,
        "shipping_fee": shipping_fee,
        "tax": tax,
        "net_revenue": net_revenue,
        "total_revenue": total_revenue,
    }
