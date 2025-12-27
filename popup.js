const statusEl = document.getElementById("status");
const runBtn = document.getElementById("run");
const copyNextBtn = document.getElementById("copyNext");
const zipPendingBtn = document.getElementById("zipPending");
const promptEl = document.getElementById("prompt");

function setStatus(msg) {
  statusEl.textContent = msg;
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

// ---- Persist prompt ----
async function loadPrompt() {
  const { savedPrompt } = await chrome.storage.local.get(["savedPrompt"]);
  if (savedPrompt) promptEl.value = savedPrompt;
}

let saveTimer = null;
function scheduleSavePrompt() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    chrome.storage.local.set({ savedPrompt: promptEl.value || "" });
  }, 250);
}

promptEl.addEventListener("input", scheduleSavePrompt);

// Load on popup open
loadPrompt();

// ---- Helper: ensure content script exists ----
async function ensureContentScript(tabId) {
  try {
    // Ping content script
    await chrome.tabs.sendMessage(tabId, { type: "PING" });
    return true;
  } catch {
    // Try to inject it (works if host_permissions allow it)
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content.js"]
      });
      // Ping again
      await chrome.tabs.sendMessage(tabId, { type: "PING" });
      return true;
    } catch (e) {
      return false;
    }
  }
}

runBtn.addEventListener("click", async () => {
  try {
    const tab = await getActiveTab();
    if (!tab?.id) return setStatus("No active tab.");

    const ok = await ensureContentScript(tab.id);
    if (!ok) {
      setStatus(
        "Content script not running on this tab.\n" +
        "Make sure you are on a shein.com page, reload the tab, and reload the extension."
      );
      return;
    }

    setStatus("Getting selected image... (click an image first)");

    const resp = await chrome.tabs.sendMessage(tab.id, { type: "GET_SELECTED_IMAGE_URL" });
    const imageUrl = resp?.imageUrl;

    if (!imageUrl) {
      setStatus("No image selected. Click the product image once (it will highlight), then try again.");
      return;
    }

    const prompt = promptEl.value || "";
    // Save immediately
    await chrome.storage.local.set({ savedPrompt: prompt });

    setStatus("Sending to server / PhotoRoom...");

    const r = await fetch("http://127.0.0.1:8787/process_image", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ imageUrl, prompt })
    });

    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      setStatus("Server error:\n" + JSON.stringify(data, null, 2));
      return;
    }

const saved = Array.isArray(data.saved_to) ? data.saved_to.join("\n") : String(data.saved_to);
setStatus(`✅ Done.\nSaved:\n${saved}\n\nPending: ${data.pending}`);
  } catch (e) {
    setStatus("Unexpected error:\n" + String(e));
  }
});

copyNextBtn.addEventListener("click", async () => {
  try {
    setStatus("Copying next pending image to clipboard...");
    const r = await fetch("http://127.0.0.1:8787/copy_trash_next", { method: "POST" });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      setStatus("Copy failed:\n" + JSON.stringify(data, null, 2));
      return;
    }
    setStatus(`✅ Copied next.\nGo to Discord and press Cmd+V.\nRemaining pending: ${data.pending}`);
  } catch (e) {
    setStatus("Copy error:\n" + String(e));
  }
});

zipPendingBtn.addEventListener("click", async () => {
  try {
    setStatus("Creating ZIP of all pending images...");
    fetch("http://127.0.0.1:8787/zip_trash_all", { method: "POST" });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) {
      setStatus("Zip failed:\n" + JSON.stringify(data, null, 2));
      return;
    }
    setStatus(`✅ ZIP created.\n${data.zip}\nFinder should open it.\nPending: ${data.pending}`);
  } catch (e) {
    setStatus("Zip error:\n" + String(e));
  }
});
