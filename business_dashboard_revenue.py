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
"""


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


def order_revenue_breakdown(o: dict) -> dict:
    """
    Trả về dict các thành phần doanh thu của 1 order, theo đúng công thức báo cáo Sapo
    "Doanh thu theo thời gian" (xem docstring module):
      item_revenue, discount, refund_value, shipping_fee, tax, net_revenue, total_revenue
    """
    item_revenue = _to_float(o.get("total_line_items_price"))
    discount = _to_float(o.get("total_discounts"))

    refund_value = 0.0
    for rf in (o.get("refunds") or []):
        for rli in (rf.get("refund_line_items") or []):
            if rli.get("subtotal") is not None:
                # subtotal = số tiền THỰC SỰ hoàn cho dòng này (đã trừ giảm giá), đã đối
                # chiếu khớp tuyệt đối với transactions[].amount thật — xem docstring module.
                refund_value += _to_float(rli.get("subtotal"))
            else:
                # Fallback phòng hờ nếu dữ liệu không có "subtotal" (không nên xảy ra với
                # dữ liệu Sapo hiện tại, nhưng giữ lại để không bị lỗi/mất dữ liệu).
                li = rli.get("line_item") or {}
                refund_value += _to_float(li.get("price")) * _to_float(rli.get("quantity"))

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
