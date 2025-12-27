let lastSelectedImageUrl = null;
let lastSelectedEl = null;

function highlight(el) {
  if (lastSelectedEl) {
    lastSelectedEl.style.outline = "";
    lastSelectedEl.style.outlineOffset = "";
  }
  lastSelectedEl = el;
  el.style.outline = "3px solid #4f46e5";
  el.style.outlineOffset = "2px";
}

function normalizeUrl(u) {
  if (!u) return null;
  try {
    // handle //cdn...
    if (u.startsWith("//")) return location.protocol + u;
    return new URL(u, location.href).href;
  } catch {
    return u;
  }
}

function getImgUrlFromElement(el) {
  if (!el) return null;

  // If user clicked the <img> directly
  if (el.tagName && el.tagName.toLowerCase() === "img") {
    return normalizeUrl(el.currentSrc || el.src);
  }

  // If user clicked inside a container that contains an img
  const img = el.querySelector && el.querySelector("img");
  if (img) return normalizeUrl(img.currentSrc || img.src);

  // Some sites use background-image
  const style = window.getComputedStyle(el);
  const bg = style && style.backgroundImage;
  if (bg && bg !== "none") {
    const m = bg.match(/url\(["']?(.*?)["']?\)/i);
    if (m && m[1]) return normalizeUrl(m[1]);
  }

  return null;
}

// Capture clicks to select an image
document.addEventListener(
  "click",
  (e) => {
    // Try the target and a few ancestors
    let el = e.target;
    for (let i = 0; i < 5 && el; i++) {
      const url = getImgUrlFromElement(el);
      if (url) {
        lastSelectedImageUrl = url;
        highlight(el.tagName.toLowerCase() === "img" ? el : (el.querySelector("img") || el));
        break;
      }
      el = el.parentElement;
    }
  },
  true
);
// Message handler
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  try {
    // 1) Ping (used by popup.js to check the content script is loaded)
    if (msg && msg.type === "PING") {
      sendResponse({ ok: true });
      return;
    }

    // 2) Return the selected image URL
    if (msg && msg.type === "GET_SELECTED_IMAGE_URL") {
      // If user didn't click anything, pick the largest visible image as fallback
      if (!lastSelectedImageUrl) {
        const imgs = Array.from(document.querySelectorAll("img"))
          .map((img) => ({
            url: normalizeUrl(img.currentSrc || img.src),
            area: (img.naturalWidth || 0) * (img.naturalHeight || 0),
          }))
          .filter((x) => x.url && x.area > 0)
          .sort((a, b) => b.area - a.area);

        if (imgs.length) lastSelectedImageUrl = imgs[0].url;
      }

      sendResponse({ imageUrl: lastSelectedImageUrl });
      return;
    }
  } catch (e) {
    // Always respond even on error
    sendResponse({ ok: false, error: String(e) });
    return;
  }

  // Default for unknown message types
  sendResponse({ ok: false });
});
