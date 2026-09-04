#!/usr/bin/env python3
"""Print the locally served Reveal.js deck to PDF."""

from playwright.sync_api import sync_playwright


URL = "http://127.0.0.1:8000/slides/?print-pdf#/"


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle")
        page.screenshot(path="/data/linux_rust_drivers.png", full_page=False)
        page.pdf(
            path="/data/linux_rust_drivers.pdf",
            print_background=True,
            display_header_footer=False,
            prefer_css_page_size=True,
        )
        browser.close()
        print("PDF saved as slides/linux_rust_drivers.pdf")


if __name__ == "__main__":
    main()
