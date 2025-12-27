import time
from pathlib import Path
from playwright.sync_api import sync_playwright

USER_DATA_DIR = str(Path.home() / "photoroom_playwright_profile")

SHEIN_URL = "https://us.shein.com/EMCGICC-Street-Life-Men-s-Y2k-Style-250-Cotton-T-Shirt-Long-Sleeve-Shirt-Space-Visual-Printing-Cotton-Graphic-T-Shirt-Men-s-Hip-Hop-Style-Long-Sleeve-T-Shirt-Fun-Vintage-Shirt-Regular-Fit-Street-Style-Halloween-Digital-Print-Ideal-Gift-p-157880403.html"

PHOTOROOM_URL = "https://app.photoroom.com/"  # web app

PROMPT_TEXT = "clean white studio background, soft shadow, centered product"

def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,   # show the browser
        )
        page = context.new_page()

        # 1) Open Shein product
        page.goto(SHEIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # Try grab og:image first (most reliable)
        og = page.locator('meta[property="og:image"]')
        image_url = og.get_attribute("content") if og.count() else None
        if not image_url:
            # fallback: biggest image
            image_url = page.locator("img").first.get_attribute("src")

        if not image_url:
            raise RuntimeError("Could not find product image URL")

        print("Image URL:", image_url)

        # 2) Download image bytes in the browser context
        img_bytes = page.request.get(image_url).body()

        # 3) Open PhotoRoom web app in a new tab
        photoroom = context.new_page()
        photoroom.goto(PHOTOROOM_URL, wait_until="domcontentloaded")
        photoroom.wait_for_timeout(3000)

        # FIRST TIME ONLY: you may need to log in manually.
        # Once logged in, this persistent profile keeps the session.

        # 4) Upload the image
        # We set the file chooser by clicking an upload button.
        # NOTE: selectors may need adjusting depending on PhotoRoom UI changes.
        upload_btn = photoroom.get_by_text("Start from photo").first
        if upload_btn.count():
            with photoroom.expect_file_chooser() as fc:
                upload_btn.click()
            file_chooser = fc.value

            # Write temp file
            tmp_path = Path("tmp_upload.jpg")
            tmp_path.write_bytes(img_bytes)
            file_chooser.set_files(str(tmp_path))
        else:
            # fallback: try any input[type=file]
            tmp_path = Path("tmp_upload.jpg")
            tmp_path.write_bytes(img_bytes)
            photoroom.set_input_files("input[type=file]", str(tmp_path))

        photoroom.wait_for_timeout(5000)

        # 5) Put your “describe change” prompt
        # This is the fragile part: we need the selector for the prompt box.
        # Try a generic strategy: click any textarea and type.
        prompt_box = photoroom.locator("textarea").first
        if prompt_box.count():
            prompt_box.click()
            prompt_box.fill(PROMPT_TEXT)
        else:
            print("Could not find a textarea for prompt. You may need to update selectors.")

        # 6) Trigger generate/apply (selector depends on UI)
        gen = photoroom.get_by_text("Generate").first
        if gen.count():
            gen.click()

        print("Now wait for processing, then export/download manually if needed.")
        print("Browser will stay open.")
        while True:
            time.sleep(1)

if __name__ == "__main__":
    main()
