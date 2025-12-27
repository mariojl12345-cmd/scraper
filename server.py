from pathlib import Path
from urllib.parse import urlparse
import time
import re
import subprocess
import zipfile
import shutil

from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

app = Flask(__name__)

# --- CORS ---
@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


USER_DATA_DIR = str(Path.home() / "photoroom_playwright_profile")
PHOTOROOM_URL = "https://app.photoroom.com/"

TMP_DIR = Path("./tmp")
TMP_DIR.mkdir(exist_ok=True)

OUT_DIR = Path("./output")
OUT_DIR.mkdir(exist_ok=True)


def is_http_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


# ---- Global Playwright state (single thread/process) ----
_pw = None
_ctx = None


def get_context():
    global _pw, _ctx
    if _pw is None:
        _pw = sync_playwright().start()
    if _ctx is None:
        _ctx = _pw.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            accept_downloads=True,
            args=["--start-maximized"],
        )
    return _ctx


def click_role_regex(page, role, pattern, timeout_ms=1500) -> bool:
    try:
        loc = page.get_by_role(role, name=re.compile(pattern, re.I))
        if loc.count():
            loc.first.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    return False


def click_text_regex(page, pattern, timeout_ms=1500) -> bool:
    try:
        loc = page.get_by_text(re.compile(pattern, re.I)).first
        if loc.count():
            loc.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    return False


def open_ai_tools_grid(pr) -> None:
    pr.wait_for_timeout(800)
    for pat in [r"ai\s*tools", r"\btools\b"]:
        if click_role_regex(pr, "button", pat) or click_text_regex(pr, pat):
            pr.wait_for_timeout(1000)
            return


def click_describe_any_change_card(pr) -> None:
    title = pr.get_by_text(re.compile(r"describe\s+any\s+change", re.I)).first

    try:
        title.wait_for(timeout=12000)
    except PWTimeout:
        pr.mouse.wheel(0, 1200)
        pr.wait_for_timeout(800)
        title.wait_for(timeout=8000)

    try:
        title.scroll_into_view_if_needed(timeout=4000)
    except Exception:
        pass

    box = title.bounding_box()
    if box:
        pr.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        pr.wait_for_timeout(1500)
        return

    for xp in [
        "xpath=ancestor::*[@role='button'][1]",
        "xpath=ancestor::*[self::button or self::a][1]",
        "xpath=ancestor::div[1]",
        "xpath=ancestor::div[2]",
        "xpath=ancestor::div[3]",
    ]:
        try:
            parent = title.locator(xp).first
            if parent.count():
                parent.click(timeout=2500)
                pr.wait_for_timeout(1500)
                return
        except Exception:
            pass


def wait_for_describe_screen(pr) -> None:
    pr.get_by_text(re.compile(r"drop a file", re.I)).first.wait_for(timeout=20000)
    pr.wait_for_timeout(600)


def upload_via_dropzone_filechooser(pr, file_path: Path) -> None:
    """
    Uses the Describe-any-change dropzone "select an image" (blue) or the "Product" button.
    """
    wait_for_describe_screen(pr)

    trigger = None
    try:
        t = pr.get_by_text(re.compile(r"select an image", re.I)).first
        if t.count():
            trigger = t
    except Exception:
        pass

    if trigger is None:
        try:
            btn = pr.get_by_role("button", name=re.compile(r"product", re.I))
            if btn.count():
                trigger = btn.first
        except Exception:
            pass

    if trigger is None:
        trigger = pr.get_by_text(re.compile(r"drop a file", re.I)).first

    with pr.expect_file_chooser(timeout=20000) as fc_info:
        trigger.click(timeout=7000)
    chooser = fc_info.value
    chooser.set_files(str(file_path))
    pr.wait_for_timeout(7000)


def fill_change_text(pr, prompt_text: str) -> None:
    if not prompt_text:
        return
    ta = pr.locator("textarea").first
    if ta.count():
        ta.click(timeout=3000)
        ta.fill(prompt_text)
        return

    ed = pr.locator('[contenteditable="true"]').first
    if ed.count():
        ed.click(timeout=3000)
        ed.fill(prompt_text)
        return


def click_send_arrow_in_prompt_box(pr) -> None:
    """
    Click the send arrow inside the prompt box (near textarea).
    """
    ta = pr.locator("textarea").first
    if ta.count() == 0:
        raise RuntimeError("Cannot find textarea to send prompt.")

    ta.scroll_into_view_if_needed(timeout=5000)
    ta.click(timeout=3000)
    pr.wait_for_timeout(200)

    container = ta.locator("xpath=ancestor::div[1]")
    for _ in range(10):
        try:
            if container.locator("button").count() >= 2:
                break
            container = container.locator("xpath=ancestor::div[1]")
        except Exception:
            break

    btns = container.locator("button:has(svg)")
    if btns.count():
        btns.nth(btns.count() - 1).click(timeout=3000)
        pr.wait_for_timeout(900)
        return

    btns2 = container.locator("button")
    if btns2.count():
        btns2.nth(btns2.count() - 1).click(timeout=3000)
        pr.wait_for_timeout(900)
        return

    raise RuntimeError("Could not find send arrow button.")


def _sips_convert_to_png(src_path: Path, dst_path: Path) -> None:
    """
    Convert downloaded bytes into a real PNG (fixes webp/jpg/blank/invalid clipboard issues).
    """
    dst_path.parent.mkdir(exist_ok=True)
    subprocess.run(
        ["sips", "-s", "format", "png", str(src_path), "--out", str(dst_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _collect_large_visible_img_srcs(pr) -> list[str]:
    imgs = pr.locator("img")
    n = imgs.count()
    out = []
    for i in range(n):
        try:
            img = imgs.nth(i)
            vis = img.evaluate("e => !!(e && e.offsetParent)")
            if not vis:
                continue

            src = (img.get_attribute("src") or "").strip()
            if not src:
                continue
            if src.startswith("data:") or src.endswith(".svg"):
                continue

            box = img.bounding_box()
            if not box:
                continue

            area = float(box["width"] * box["height"])
            if area < 60000:
                continue

            out.append(src)
        except Exception:
            continue
    return out


def save_two_latest_results_from_page(pr, before_srcs: set[str], timeout_ms: int = 120000) -> list[str]:
    """
    Wait until TWO NEW large images appear whose src was NOT present before generation.
    Then download those two (the last two new ones) and convert to PNG.
    """
    start = time.time()

    while True:
        now_srcs = _collect_large_visible_img_srcs(pr)
        new_srcs = [s for s in now_srcs if s not in before_srcs]

        # de-dupe preserve order
        seen = set()
        new_unique = []
        for s in new_srcs:
            if s in seen:
                continue
            seen.add(s)
            new_unique.append(s)

        if len(new_unique) >= 2:
            chosen = new_unique[-2:]  # last two new images = latest results
            saved = []
            ts = int(time.time())
            for idx, src in enumerate(chosen, start=1):
                r = pr.request.get(src)
                b = r.body()

                raw_path = TMP_DIR / f"raw_{ts}_{idx}"
                raw_path.write_bytes(b)

                out_path = OUT_DIR / f"result_{ts}_{idx}.png"
                _sips_convert_to_png(raw_path, out_path)

                try:
                    raw_path.unlink(missing_ok=True)
                except Exception:
                    pass

                saved.append(str(out_path))
            return saved

        if (time.time() - start) * 1000 > timeout_ms:
            raise RuntimeError(
                "Timed out waiting for 2 generated results. "
                f"Found {len(new_unique)} new candidates."
            )

        pr.wait_for_timeout(800)


def copy_png_to_clipboard_macos(path: str) -> None:
    """
    Copy a real PNG to clipboard so Discord can Cmd+V.
    """
    script = f'''
    set theFile to POSIX file "{path}"
    set the clipboard to (read theFile as PNG picture)
    '''
    subprocess.run(["osascript", "-e", script], check=True, timeout=8)


def move_to_trash(path: Path) -> Path:
    """
    Move a file to macOS Trash so it won't be copied again.
    """
    trash = Path.home() / ".Trash"
    trash.mkdir(exist_ok=True)
    dest = trash / path.name
    if dest.exists():
        dest = trash / f"{path.stem}_{int(time.time())}{path.suffix}"
    shutil.move(str(path), str(dest))
    return dest


def list_output_pngs() -> list[Path]:
    files = sorted(OUT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
    return [p for p in files if p.is_file()]


@app.get("/")
def home():
    return "Server running. POST /process_image. POST /copy_trash_next. POST /zip_trash_all. GET /output_count", 200


@app.get("/output_count")
def output_count():
    return jsonify({"ok": True, "count": len(list_output_pngs())})


@app.get("/open_photoroom")
def open_photoroom():
    ctx = get_context()
    pr = ctx.new_page()
    pr.goto(PHOTOROOM_URL, wait_until="domcontentloaded")
    pr.bring_to_front()
    return jsonify({"ok": True, "message": "PhotoRoom opened in Playwright browser. Log in there once."})


@app.route("/process_image", methods=["OPTIONS"])
def process_image_options():
    return ("", 204)


@app.post("/process_image")
def process_image():
    data = request.get_json(force=True) or {}
    image_url = (data.get("imageUrl") or "").strip()
    prompt_text = (data.get("prompt") or "").strip()

    if not is_http_url(image_url):
        return jsonify({"ok": False, "error": "imageUrl must be a valid http(s) URL"}), 400

    try:
        ctx = get_context()

        # Download selected product image bytes
        page = ctx.new_page()
        resp = page.request.get(image_url)
        img_bytes = resp.body()

        tmp_path = TMP_DIR / "upload_input"
        tmp_path.write_bytes(img_bytes)

        # Convert input to PNG for stable upload
        upload_png = TMP_DIR / "upload.png"
        _sips_convert_to_png(tmp_path, upload_png)

        # Open PhotoRoom
        pr = ctx.new_page()
        pr.goto(PHOTOROOM_URL, wait_until="domcontentloaded")
        pr.wait_for_timeout(2500)
        pr.bring_to_front()

        # AI Tools -> Describe any change
        open_ai_tools_grid(pr)
        click_describe_any_change_card(pr)

        # Upload
        upload_via_dropzone_filechooser(pr, upload_png)

        # Capture image srcs BEFORE generation (to exclude upload preview etc.)
        before_srcs = set(_collect_large_visible_img_srcs(pr))

        # Prompt + send
        fill_change_text(pr, prompt_text)
        click_send_arrow_in_prompt_box(pr)

        # Wait for and save the last two generated results
        saved_paths = save_two_latest_results_from_page(pr, before_srcs, timeout_ms=120000)

        return jsonify({"ok": True, "saved_to": saved_paths, "output_count": len(list_output_pngs())})

    except Exception as e:
        return jsonify({"ok": False, "error": "Automation failed", "details": str(e)}), 500


@app.post("/copy_trash_next")
def copy_trash_next():
    files = list_output_pngs()
    if not files:
        return jsonify({"ok": False, "error": "No PNGs in ./output to copy."}), 400

    next_file = files[0]
    try:
        copy_png_to_clipboard_macos(str(next_file))
        trashed_to = move_to_trash(next_file)
        return jsonify({"ok": True, "copied": True, "trashed_to": str(trashed_to), "remaining": len(list_output_pngs())})
    except Exception as e:
        return jsonify({"ok": False, "error": "Copy/trash failed", "details": str(e)}), 500


@app.post("/zip_trash_all")
def zip_trash_all():
    files = list_output_pngs()
    if not files:
        return jsonify({"ok": False, "error": "No PNGs in ./output to zip."}), 400

    zip_path = OUT_DIR / f"output_{int(time.time())}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in files:
                zf.write(p, arcname=p.name)

        # Trash originals (leave zip)
        for p in files:
            try:
                move_to_trash(p)
            except Exception:
                pass

        # Reveal zip in Finder
        try:
            subprocess.run(["open", "-R", str(zip_path)], check=False)
        except Exception:
            pass

        return jsonify({"ok": True, "zip": str(zip_path), "remaining": len(list_output_pngs())})
    except Exception as e:
        return jsonify({"ok": False, "error": "Zip/trash failed", "details": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8787, debug=False, use_reloader=False, threaded=False)
