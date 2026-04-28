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
                page.fill('input[placeholder="Search Orders."]', customer_name, timeout=8000)
                page.keyboard.press("Enter")
            except PlaywrightTimeout:
                _save_debug_screenshot(page, "debug_orders.png")
                raise RuntimeError(
                    "Could not find the order search field. "
                    "A screenshot was saved to debug_orders.png."
                )

            page.wait_for_timeout(3000)

            # ── Step 4: Click the most recent (first) order ──────────────────
            order_link = None
            for selector in [
                "table tbody tr:first-child a",
                ".order-list .order-item:first-child a",
                "[data-testid='order-row']:first-child a",
                ".orders-table tr:nth-child(2) a",  # skip header row
            ]:
                try:
                    order_link = page.locator(selector).first
                    order_link.wait_for(timeout=5000)
                    break
                except PlaywrightTimeout:
                    order_link = None

            if order_link is None:
                _save_debug_screenshot(page, "debug_order_list.png")
                raise RuntimeError(
                    f"No orders found for '{customer_name}', or could not locate order rows. "
                    "A screenshot was saved to debug_order_list.png."
                )

            # Grab order number and date from the row before clicking
            order_row_text = page.locator("table tbody tr:first-child").text_content() or ""
            order_link.click()
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
    Tries multiple selector patterns to handle CDW page variations.
    """
    items = []

    # Pattern A: table rows with product name and tracking in cells
    rows = page.locator("table.order-items tbody tr, table.line-items tbody tr").all()
    if rows:
        for row in rows:
            cells = row.locator("td").all()
            if len(cells) < 2:
                continue
            name = cells[0].text_content().strip()
            # Find a cell that looks like it has a tracking number (UPS/FedEx/USPS format)
            tracking = ""
            for cell in cells[1:]:
                text = cell.text_content().strip()
                if _looks_like_tracking(text):
                    tracking = text
                    break
                # Also check for links with tracking text
                link = cell.locator("a[href*='tracking'], a[href*='track']").first
                try:
                    tracking = link.text_content(timeout=500).strip()
                    if tracking:
                        break
                except Exception:
                    pass
            if name:
                items.append((name, tracking))
        if items:
            return items

    # Pattern B: card/list style items
    item_blocks = page.locator(
        ".line-item, .order-item, [data-testid='line-item'], .product-row"
    ).all()
    for block in item_blocks:
        name = _try_text(block, [
            ".product-name", ".item-description", ".product-title",
            "[data-testid='product-name']", "h3", "h4",
        ]) or ""
        tracking = _try_text(block, [
            ".tracking-number", "[data-testid='tracking-number']",
            "a[href*='tracking']", "a[href*='track']",
        ]) or ""
        if name:
            items.append((name.strip(), tracking.strip()))

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
