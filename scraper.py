import os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

CDW_EMAIL = os.environ.get("CDW_EMAIL", "")
CDW_PASSWORD = os.environ.get("CDW_PASSWORD", "")

CDW_LOGIN_URL = "https://www.cdw.com/account/logon"
CDW_ORDERS_URL = "https://www.cdw.com/account/orders"


def get_order_info(customer_name: str) -> str:
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
        page = context.new_page()

        try:
            # ── Step 1: Log in ────────────────────────────────────────────────
            page.goto(CDW_LOGIN_URL, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=15000)

            # Fill email — selector may need adjustment; saving a debug screenshot if it fails
            try:
                page.fill('input[name="username"]', CDW_EMAIL, timeout=8000)
            except PlaywrightTimeout:
                _save_debug_screenshot(page, "debug_login.png")
                raise RuntimeError(
                    "Could not find the email field on the login page. "
                    "A screenshot was saved to debug_login.png — check the selector."
                )

            page.fill('input[name="password"]', CDW_PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle", timeout=20000)

            # ── Step 2: Navigate to orders ───────────────────────────────────
            page.goto(CDW_ORDERS_URL, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=15000)

            # ── Step 3: Search by customer name ──────────────────────────────
            # Try common search input patterns; CDW uses a "Search by" or filter field
            search_filled = False
            for selector in [
                'input[placeholder*="Search"]',
                'input[placeholder*="search"]',
                'input[aria-label*="search" i]',
                'input[name*="search" i]',
                'input[id*="search" i]',
            ]:
                try:
                    page.fill(selector, customer_name, timeout=3000)
                    page.keyboard.press("Enter")
                    search_filled = True
                    break
                except PlaywrightTimeout:
                    continue

            if not search_filled:
                _save_debug_screenshot(page, "debug_orders.png")
                raise RuntimeError(
                    "Could not find the order search field. "
                    "A screenshot was saved to debug_orders.png."
                )

            page.wait_for_load_state("networkidle", timeout=15000)

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
            page.wait_for_load_state("networkidle", timeout=15000)

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
