"""
Live recon: load the Vue Enschede page, capture every network request that
looks like an API/data call, and dump method + URL + key request headers +
response status. Lets us see what the front-end really hits now.
"""
import os
from playwright.sync_api import sync_playwright

VUE_LISTING_URL = "https://www.vuecinemas.nl/cinema/enschede/nu-in-de-bioscoop"

INTERESTING = ("/api/", "microservice", "showing", "film", "session", "graphql", ".json")


def main():
    hits = []

    with sync_playwright() as pw:
        user_data_dir = os.path.join(os.path.expanduser("~"), ".vue_poll_browser")
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir, headless=False, channel="msedge", locale="nl-NL",
        )
        page = ctx.new_page()

        def on_response(resp):
            url = resp.url
            if any(k in url.lower() for k in INTERESTING):
                req = resp.request
                resp_headers = {}
                body = ""
                if resp.status == 403 and "vuecinemas.nl" in url:
                    try:
                        resp_headers = dict(resp.headers)
                    except Exception:
                        pass
                    try:
                        body = resp.text()[:400]
                    except Exception:
                        pass
                hits.append({
                    "status": resp.status,
                    "method": req.method,
                    "url": url,
                    "headers": dict(req.headers),
                    "resp_headers": resp_headers,
                    "body": body,
                })

        page.on("response", on_response)

        print(f"Loading {VUE_LISTING_URL} ...")
        page.goto(VUE_LISTING_URL, wait_until="networkidle", timeout=60000)

        # Automation signals the bot-detector looks for
        wd = page.evaluate("() => navigator.webdriver")
        print(f"navigator.webdriver = {wd}")

        # accept cookie banner
        for sel in ("#onetrust-accept-btn-handler",
                    "button:has-text('Accepteren')",
                    "button:has-text('Alles accepteren')"):
            try:
                page.locator(sel).first.click(timeout=2000)
                page.wait_for_timeout(1000)
                break
            except Exception:
                pass

        page.wait_for_timeout(4000)
        ctx.close()

    print(f"\n==== {len(hits)} interesting requests ====\n")
    for h in hits:
        print(f"[{h['status']}] {h['method']} {h['url']}")

    # Dump 403 response headers + body to identify the bot-detection vendor
    print("\n==== 403 response headers / body ====\n")
    for h in hits:
        if h["status"] == 403 and h.get("resp_headers"):
            print(f"[403] {h['method']} {h['url']}")
            for k, v in h["resp_headers"].items():
                print(f"    {k}: {v}")
            if h.get("body"):
                print(f"    BODY: {h['body']!r}")
            print()
            break  # one is enough to identify the vendor


if __name__ == "__main__":
    main()
