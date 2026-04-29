import os
import json
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

CDW_COOKIES_JSON = os.environ.get("CDW_COOKIES", "")
CDW_ORDERS_URL = "https://www.cdw.com/accountcenter/orders/all"


def get_order_info(customer_name: str) -> str:
    if not CDW_COOKIES_JSON:
        raise RuntimeError("CDW_COOKIES environment variable is not set.")

    try:
        raw_cookies = json.loads(CDW_COOKIES_JSON)
    except json.JSONDecodeError:
        raise RuntimeError("CDW_COOKIES is not valid JSON. Re-export your cookies and update the variable.")

    # Normalize cookies to the format Playwright expects
    same_site_map = {
        "no_restriction": "None",
        "unspecified": "None",
        "lax": "Lax",
        "strict": "Strict",
        "none": "None",
    }
    cookies = []
    for c in raw_cookies:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".cdw.com"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
            "sameSite": same_site_map.get(str(c.get("sameSite", "")).lower(), "None"),
        }
        if "expirationDate" in c:
            cookie["expires"] = c["expirationDate"]
        cookies.append(cookie)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        # Load session cookies so we skip the login page entirely
        context.add_cookies(cookies)
        page = context.new_page()

        try:
            # ── Step 1: Navigate directly to orders ──────────────────────────
            page.goto(CDW_ORDERS_URL, wait_until="load")
            page.wait_for_timeout(4000)

            # If cookies expired we'll land back on the login page
            if "logon" in page.url.lower():
                raise RuntimeError(
                    "CDW session has expired. Please re-export your cookies from Chrome "
                    "and update the CDW_COOKIES variable in Railway."
                )

            # Dismiss any welcome popups
            for sel in ['button[aria-label*="close" i]', 'button[aria-label*="dismiss" i]',
                        '.modal-close', '.popup-close', 'button.close', '[data-dismiss="modal"]']:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    pass

            # ── Step 2: Search by customer name ──────────────────────────────
            try:
                page.fill('input[aria-label="Search Orders in Grid"]', customer_name, timeout=8000)
                page.keyboard.press("Enter")
            except PlaywrightTimeout:
                _save_debug_screenshot(page, "debug_orders.png")
                raise RuntimeError(
                    "Could not find the order search field. "
                    "A screenshot was saved to debug_orders.png."
                )

            page.wait_for_timeout(3000)

            # ── Step 4: Click the most recent (first) order ──────────────────
            # DevExtreme grid uses aria-rowindex on data rows
            first_row = page.locator("tr[aria-rowindex='1']").first
            try:
                first_row.wait_for(timeout=8000)
            except PlaywrightTimeout:
                _save_debug_screenshot(page, "debug_order_list.png")
                raise RuntimeError(
                    f"No orders found for '{customer_name}', or could not locate order rows. "
                    "A screenshot was saved to debug_order_list.png."
                )

            # Try clicking a link inside the row, or the row itself
            order_link = first_row.locator("a").first
            try:
                order_link.wait_for(timeout=3000)
                order_link.click()
            except PlaywrightTimeout:
                first_row.click()

            page.wait_for_load_state("load", timeout=15000)
            page.wait_for_timeout(3000)

            # ── Step 5: Extract order header info ────────────────────────────
            order_number = _try_text(page, [
                "[data-testid='order-number']",
                ".order-number",
                "h1",
                ".order-header .order-id",
            ]) or "N/A"

            order_date = _try_text(page, [
                "[data-testid='order-date']",
                ".order-date",
                ".order-header .date",
                "td.order-date",
            ]) or "N/A"

            # ── Step 6: Extract line items + tracking numbers ────────────────
            items = _extract_items(page)

            if not items:
                _save_debug_screenshot(page, "debug_order_detail.png")
                raise RuntimeError(
                    "Found the order but could not extract line items. "
                    "A screenshot was saved to debug_order_detail.png."
                )

            # ── Step 7: Format the Slack response ────────────────────────────
            lines = [f":package: *Order {order_number}* — {customer_name} ({order_date})"]
            for item_name, tracking in items:
                if tracking:
                    lines.append(f"• {item_name}\n  :truck: `{tracking}`")
                else:
                    lines.append(f"• {item_name}\n  :truck: _tracking not yet available_")

            return "\n".join(lines)

        finally:
            browser.close()


def _extract_items(page) -> list[tuple[str, str]]:
    """
    Returns a list of (item_name, tracking_number) tuples.
    Uses CDW's order detail page structure where:
    - Product name is in a link pointing to /shop/products/default.aspx
    - Tracking number is in a link with class narvar-tracking-modal
    Each table row may have multiple tracking links (multiple shipments).
    """
    items = []

    # Find all rows that contain a product link
    rows = page.locator("table tr").all()
    for row in rows:
        # Check if this row has a product link
        product_links = row.locator("a[href*='/shop/products/default.aspx']").all()
        if not product_links:
            continue

        name = product_links[0].text_content().strip()
        if not name:
            continue

        # Find all tracking links in this row
        tracking_links = row.locator("a.narvar-tracking-modal, a[href*='TrackShipment']").all()
        if tracking_links:
            for t_link in tracking_links:
                tracking = t_link.text_content().strip()
                if tracking:
                    items.append((name, tracking))
        else:
            items.append((name, ""))

    return items


def _try_text(locator_or_page, selectors: list[str]) -> str | None:
    for sel in selectors:
        try:
            el = locator_or_page.locator(sel).first
            text = el.text_content(timeout=2000)
            if text and text.strip():
                return text.strip()
        except Exception:
            continue
    return None


def _looks_like_tracking(text: str) -> bool:
    """Rough check for UPS (1Z...), FedEx (12/15/20/22 digit), USPS formats."""
    import re
    text = text.strip().replace(" ", "")
    return bool(re.match(r"^(1Z[A-Z0-9]{16}|\d{12}|\d{15}|\d{20}|\d{22}|9[2345]\d{18,20})$", text))


def _save_debug_screenshot(page, filename: str):
    try:
        page.screenshot(path=filename)
    except Exception:
        pass
