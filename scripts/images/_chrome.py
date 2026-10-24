"""Minimal headless Chrome driver over the DevTools protocol (CDP).

The only third-party dependency is ``websocket-client``. No Playwright or
Selenium is needed, so it runs on a stock GitHub runner (``google-chrome`` is
preinstalled on ``ubuntu-latest``), on macOS and on Windows with a normal
Chrome install.

Chrome is found in this order:

1. the ``CHROME`` environment variable (full path to the executable);
2. the usual install locations for the current platform;
3. ``google-chrome``, ``google-chrome-stable``, ``chromium``, ``chromium-browser``
   or ``chrome`` on ``PATH``.

Used by ``shot.py`` (remote pages), ``render.py`` (local HTML and Mermaid) and
``scripts/make_covers.py``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from types import TracebackType
from typing import Any

log = logging.getLogger(__name__)

PATH_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")


def _platform_candidates() -> list[pathlib.Path]:
    """Default install locations for the running platform."""
    if sys.platform == "win32":
        roots = [os.environ.get(v) for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        return [
            pathlib.Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe" for r in roots if r
        ]
    if sys.platform == "darwin":
        apps = [pathlib.Path("/Applications"), pathlib.Path.home() / "Applications"]
        return [
            a / name
            for a in apps
            for name in (
                "Google Chrome.app/Contents/MacOS/Google Chrome",
                "Chromium.app/Contents/MacOS/Chromium",
            )
        ]
    return [
        pathlib.Path(p)
        for p in (
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        )
    ]


def find_chrome() -> str:
    """Return the path of a Chrome/Chromium executable or raise ``FileNotFoundError``."""
    env = os.environ.get("CHROME")
    if env:
        if pathlib.Path(env).exists() or shutil.which(env):
            return shutil.which(env) or env
        raise FileNotFoundError(f"CHROME={env!r} does not exist")
    for candidate in _platform_candidates():
        if candidate.exists():
            return str(candidate)
    for name in PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("Chrome not found: install it or set the CHROME environment variable")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Chrome:
    """One headless Chrome with one tab, used as a context manager.

    A fresh, empty profile is created for every run, so pages are always seen
    logged out and nothing from a personal browser profile can leak into a capture.
    """

    def __init__(
        self, width: int = 1280, height: int = 800, scale: float = 2.0, dark: bool = False
    ) -> None:
        self.width, self.height, self.scale, self.dark = width, height, scale, dark
        self._id = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._profile = ""
        self._ws: Any = None

    def __enter__(self) -> Chrome:
        import websocket  # pip install websocket-client

        port = _free_port()
        self._profile = tempfile.mkdtemp(prefix="blog-chrome-")
        args = [
            find_chrome(),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--mute-audio",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self._profile}",
            f"--window-size={self.width},{self.height}",
            "about:blank",
        ]
        if sys.platform.startswith("linux") and os.geteuid() == 0:  # containers run as root
            args.insert(1, "--no-sandbox")
        log.debug("starting %s", args[0])
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws_url = None
        for _ in range(150):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
                    tabs = [t for t in json.load(r) if t.get("type") == "page"]
                if tabs:
                    ws_url = tabs[0]["webSocketDebuggerUrl"]
                    break
            except OSError:
                pass
            time.sleep(0.1)
        if not ws_url:
            self.__exit__(None, None, None)
            raise RuntimeError("Chrome did not start")
        self._ws = websocket.create_connection(ws_url, timeout=90, suppress_origin=True)
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send(
            "Emulation.setDeviceMetricsOverride",
            width=self.width,
            height=self.height,
            deviceScaleFactor=self.scale,
            mobile=False,
        )
        self.send(
            "Emulation.setEmulatedMedia",
            features=[
                {"name": "prefers-color-scheme", "value": "dark" if self.dark else "light"},
                {"name": "prefers-reduced-motion", "value": "reduce"},
            ],
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._ws is not None:
                self._ws.close()
        finally:
            if self._proc is not None:
                self._proc.terminate()
                try:
                    self._proc.wait(10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            shutil.rmtree(self._profile, ignore_errors=True)

    def send(self, method: str, **params: Any) -> dict[str, Any]:
        """Send one CDP command and wait for its result."""
        self._id += 1
        my_id = self._id
        self._ws.send(json.dumps({"id": my_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") == my_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return dict(msg.get("result", {}))

    def eval(self, expr: str, await_promise: bool = False) -> Any:
        """Evaluate a JavaScript expression in the page and return its value."""
        r = self.send(
            "Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=await_promise
        )
        if "exceptionDetails" in r:
            raise RuntimeError(f"JavaScript error: {r['exceptionDetails'].get('text')}")
        return r.get("result", {}).get("value")

    def goto(self, url: str, wait: float = 1.5, timeout: float = 45) -> None:
        """Open ``url``, wait for the load event and web fonts, then ``wait`` seconds more."""
        self.send("Page.navigate", url=url)
        deadline = time.time() + timeout
        while time.time() < deadline and self.eval("document.readyState") != "complete":
            time.sleep(0.2)
        self.eval(
            "document.fonts ? document.fonts.ready.then(() => true) : true", await_promise=True
        )
        time.sleep(wait)  # late layout, lazy images

    def add_css(self, css: str) -> None:
        """Inject a style sheet into the current page."""
        self.eval(
            "(()=>{const s=document.createElement('style');"
            f"s.textContent={json.dumps(css)};document.head.appendChild(s);}})()"
        )

    def set_viewport(self, width: int, height: int) -> None:
        """Change the emulated viewport size."""
        self.width, self.height = width, height
        self.send(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=self.scale,
            mobile=False,
        )

    def element_clip(self, selector: str, pad: int = 0) -> dict[str, float]:
        """Bring the first element matching ``selector`` into view and return its clip.

        The capture must not depend on content outside the rendered viewport:
        lazy images below the fold, ``content-visibility`` and pages that scroll
        inside a container instead of the window all leave that area blank. So
        the viewport is made tall enough for the element, the element is scrolled
        into the middle of it (inside scrolling containers too), and images in it
        get time to load before the clip is measured in page coordinates.
        """
        size = self.eval(f"(()=>{{{_FIND % json.dumps(selector)} return {_BOX}}})()")
        if not size:
            raise LookupError(f"selector not found: {selector}")
        needed = int(size["h"]) + 2 * pad + 2
        if needed > self.height:
            limit = int(MAX_CAPTURE_PX / self.scale)
            if needed > limit:
                log.warning("%s is %d px tall; capturing the top %d px", selector, needed, limit)
            self.set_viewport(self.width, min(needed, limit))
        box = self.eval(_SCROLL % json.dumps(selector), await_promise=True)
        x, y = max(box["x"] - pad, 0), max(box["y"] - pad, 0)
        return {
            "x": x,
            "y": y,
            "width": min(box["w"] + 2 * pad, box["pw"] - x),
            "height": min(box["h"] + 2 * pad, box["ph"] - y),
            "scale": 1,
        }

    def screenshot(
        self, path: pathlib.Path, clip: dict[str, float] | None = None, full_page: bool = False
    ) -> pathlib.Path:
        """Save a PNG of the viewport, of ``clip`` (page coordinates) or of the whole page."""
        params: dict[str, Any] = {"format": "png", "captureBeyondViewport": full_page and not clip}
        if full_page and not clip:
            size = self.eval(
                "({w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight})"
            )
            clip = {"x": 0, "y": 0, "width": size["w"], "height": size["h"], "scale": 1}
        if clip:
            params["clip"] = clip
        data = self.send("Page.captureScreenshot", **params)["data"]
        path.write_bytes(base64.b64decode(data))
        return path


# Largest capture Chrome renders reliably, in device pixels.
MAX_CAPTURE_PX = 16_000
_FIND = "const e=document.querySelector(%s);if(!e)return null;const b=e.getBoundingClientRect();"
_BOX = "{w:b.width,h:b.height};"
# Scroll the element into view without animation, wait for its images (at most
# 10 s) and two frames, then report its box in page coordinates.
_SCROLL = """(async()=>{const e=document.querySelector(%s);
e.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
const imgs=[...e.querySelectorAll('img')];if(e.tagName==='IMG')imgs.push(e);
imgs.forEach(i=>{i.loading='eager';});
const until=Date.now()+10000;
while(imgs.some(i=>!i.complete)&&Date.now()<until)await new Promise(r=>setTimeout(r,100));
await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
const b=e.getBoundingClientRect(),d=document.documentElement;
return {x:b.left+scrollX,y:b.top+scrollY,w:b.width,h:b.height,
pw:Math.max(d.scrollWidth,innerWidth),ph:Math.max(d.scrollHeight,innerHeight)};})()"""


def screenshot_file(html_file: pathlib.Path, png: pathlib.Path, width: int, height: int) -> None:
    """Capture a local HTML file at an exact window size with Chrome's ``--screenshot`` flag."""
    profile = tempfile.mkdtemp(prefix="blog-chrome-")
    try:
        subprocess.run(
            [
                find_chrome(),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                f"--screenshot={png.resolve()}",
                html_file.resolve().as_uri(),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)
