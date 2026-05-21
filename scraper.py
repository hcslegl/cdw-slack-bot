import os
import json
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from session import get_cookies, set_cookies

CDW_ORDERS_URL = "https://www.cdw.com/accountcenter/orders/all"

_SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified": "None",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}


def _normalize_cookies(raw_cookies: list) -> list:
    cookies = []
    for c in raw_cookies:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".cdw.com"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
            "sameSite": _SAME_SITE_MAP.get(str(c.get("sameSite", "")).lower(), "None"),
        }
        if "expirationDate" in c:
            cookie["expires"] = c["expirationDate"]
        elif "expires" in c:
            cookie["expires"] = c["expires"]
        cookies.append(cookie)
    return cookies


def _login(page):
    """Log into CDW using CDW_EMAIL and CDW_PASSWORD env vars."""
    email = os.environ.get("CDW_EMAIL", "")
    password = os.environ.get("CDW_PASSWORD", "")

    if not email or not password:
        raise RuntimeError(
            "CDW session expired and CDW_EMAIL/CDW_PASSWORD env vars are not set. "
            "Add these to Railway to enable auto-login."
        )

    try:
        page.fill(
            'input[name="username"], input[id*="username" i], input[name*="username" i], '
            'input[type="email"], input[name="email"], input[id*="email" i], input[name*="email" i]',
            email,
            timeout=8000,
        )
    except PlaywrightTimeout:
        _save_debug_screenshot(page, "debug_login.png")
        raise RuntimeError("Could not find the username/email field on the CDW login page.")

    try:
        page.fill(
            'input[type="password"], input[name="password"], input[id*="password" i]',
            password,
            timeout=8000,
        )
    except PlaywrightTimeout:
        _save_debug_screenshot(page, "debug_login.png")
        raise RuntimeError("Could not find the password field on the CDW login page.")

    page.keyboard.press("Enter")
    page.wait_for_load_state("load", timeout=15000)
    page.wait_for_timeout(3000)

    if "logon" in page.url.lower() or "login" in page.url.lower():
        _save_debug_screenshot(page, "debug_login.png")
        raise RuntimeError(
            "CDW login failed. Check that CDW_EMAIL and CDW_PASSWORD are correct in Railway."
        )


def get_order_info(customer_name: str) -> str:
    raw_cookies = get_cookies()

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

        if raw_cookies:
            context.add_cookies(_normalize_cookies(raw_cookies))

        page = context.new_page()

        try:
            # ── Step 1: Navigate to orders ───────────────────────────────────
            page.goto(CDW_ORDERS_URL, wait_until="load")
            page.wait_for_timeout(4000)

            # ── Step 2: Auto-login if session expired ────────────────────────
            if "logon" in page.url.lower() or "login" in page.url.lower():
                _login(page)

                # Cache the fresh session cookies so subsequent requests are faster
                fresh_cookies = context.cookies()
                if fresh_cookies:
                    set_cookies(json.dumps(fresh_cookies))

                page.goto(CDW_ORDERS_URL, wait_until="load")
                page.wait_for_timeout(4000)

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

            # ── Step 3: Search by customer name ──────────────────────────────
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
            first_row = page.locator("tr[aria-rowindex='1']").first
            try:
                first_row.wait_for(timeout=8000)
            except PlaywrightTimeout:
                _save_debug_screenshot(page, "debug_order_list.png")
                raise RuntimeError(
                    f"No orders found for '{customer_name}', or could not locate order rows. "
                    "A screenshot was saved to debug_order_list.png."
                )

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

    rows = page.locator("table tr").all()
    for row in rows:
        product_links = row.locator("a[href*='/shop/products/default.aspx']").all()
        if not product_links:
            continue

        name = ""
        for pl in product_links:
            text = pl.text_content().strip()
            if text:
                name = text
                break
        if not name:
            continue

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
