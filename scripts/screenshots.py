"""End-to-end smoke test and README screenshots, with headless Chromium (Playwright).

Needs a running stack, e.g. `docker compose up` (http://localhost:8080) or uvicorn + `npm run dev`
(http://localhost:5173). Playwright is not a project dependency: pip install playwright && playwright install chromium.

    python scripts/screenshots.py [base_url] [--engine template|llm|auto]

Each step asserts what a user would see; the script exits non-zero if the page does not behave.
"""
import argparse
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def ask(page, question: str, engine: str):
    page.select_option("select", engine)
    page.fill("#q", question)
    page.click("button[type=submit]")
    expect(page.locator("button[type=submit]")).to_have_text("Ask", timeout=180_000)  # "Working…" while waiting
    expect(page.locator(".answer")).to_be_visible()
    page.wait_for_timeout(4000)  # map fit animation and basemap tiles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url", nargs="?", default="http://localhost:8080")
    ap.add_argument("--engine", default="template")
    ap.add_argument("--llm", action="store_true", help="also take a screenshot of a language-model answer")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 860}, device_scale_factor=1)
        with page.expect_response(lambda r: r.url.endswith("/api/layers/stops") and r.ok):
            page.goto(a.base_url)
        page.wait_for_function("document.querySelector('.maplibregl-canvas') !== null")
        page.wait_for_timeout(4000)  # basemap tiles
        expect(page.locator(".maplibregl-ctrl-attrib")).to_contain_text(re.compile("OpenStreetMap|OpenFreeMap", re.I))
        page.screenshot(path=str(OUT / "overview.png"))

        # a spatial question: result points on the map, SQL and row count in the panel
        ask(page, "Which bus stops are within 300 m of stop 08057?", a.engine)
        expect(page.locator(".answer .sql")).to_contain_text("SELECT")
        expect(page.locator(".answer .meta").first).to_contain_text("rows")
        page.screenshot(path=str(OUT / "answer_within_300m.png"))

        # a coverage question: subzone polygons
        ask(page, "List the subzones in Bukit Timah where less than 50% of residents are within a 400 m walk of a stop.",
            a.engine)
        expect(page.locator(".answer table tbody tr").first).to_be_visible()
        page.screenshot(path=str(OUT / "answer_coverage_gaps.png"))

        # a refusal
        ask(page, "DROP TABLE stops;", a.engine)
        expect(page.locator(".answer.refused")).to_be_visible()
        page.screenshot(path=str(OUT / "refusal.png"))

        # click a stop on the map: details and desire lines. A one-stop answer centres the map on Boon Lay Int (22009).
        ask(page, "What are the average weekday boardings at stop 22009?", a.engine)
        page.wait_for_timeout(1000)
        box = page.locator(".map").bounding_box()
        with page.expect_response(lambda r: "/api/stops/22009/od" in r.url and r.ok):
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        expect(page.locator(".stop")).to_contain_text("Boon Lay Int")
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / "stop_od_lines.png"))
        page.click("button[aria-label='Close stop details']")

        # the language model on a question it answered correctly in the evaluation (a01); needs a reachable model
        if a.llm:
            ask(page, "Which 10 bus stops have the most weekday boardings?", "llm")
            expect(page.locator(".answer .meta").first).to_contain_text("engine: llm")
            page.screenshot(path=str(OUT / "answer_llm_top10.png"))

        # phone width
        phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                 has_touch=True)
        with phone.expect_response(lambda r: r.url.endswith("/api/layers/stops") and r.ok):
            phone.goto(a.base_url)
        phone.wait_for_timeout(4000)
        ask(phone, "Which 10 bus stops have the most weekday boardings?", a.engine)
        width = phone.evaluate("document.documentElement.scrollWidth")
        assert width <= 390, f"horizontal scroll at phone width: {width}px"
        phone.screenshot(path=str(OUT / "phone_map.png"))
        phone.locator(".answer").scroll_into_view_if_needed()
        phone.screenshot(path=str(OUT / "phone_answer.png"))
        browser.close()
    print("screenshots in", OUT)


if __name__ == "__main__":
    main()
