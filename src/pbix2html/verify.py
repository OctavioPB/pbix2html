"""
HTML verifier: finds the problems a converted report has that a person would spot by eye.

Two layers, both working from the HTML file alone (it embeds its spec and data as JSON):

* static checks on the spec (no browser): visuals that overlap, sit outside the page or are tiny,
  kinds with no renderer, visuals with no data;
* rendered checks in a real browser (Playwright, optional): every page is opened and measured from
  the live DOM. Text the same colour as its background, text hidden under another visual or a picture,
  clipped text, failed or empty visuals, broken images, and chart labels that will not fit.

Output: `verify_report.md` + `verify_report.json`, and one screenshot per page with the findings boxed
and numbered. Nothing here judges *fidelity to the Power BI original* (no reference exists offline): it
finds what is objectively wrong or suspicious in the HTML, so a person compares only the flagged visuals.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROBE_JS = (Path(__file__).parent / "templates" / "verify_probe.js").read_text(encoding="utf-8")
SEVERITIES = ("error", "warn", "info")
NO_DATA_KINDS = {"static", "text", "slicer", "tooltip", "unsupported", "pending", "custom"}
CONTENT_SKIP = {"static", "tooltip"}


@dataclass
class Finding:
    severity: str                 # error | warn | info
    rule: str
    page: str
    visual: str | None
    message: str
    hint: str = ""
    title: str | None = None
    kind: str | None = None
    rect: list[int] | None = None       # x, y, w, h in px, relative to the page (browser checks)
    n: int = 0                          # number shown on the screenshot


@dataclass
class VerifyResult:
    findings: list[Finding] = field(default_factory=list)
    screenshots: dict[str, str] = field(default_factory=dict)
    browser: str = "not run"
    pages: int = 0
    report: Path | None = None

    def count(self, severity: str) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def ok(self) -> bool:
        return self.count("error") == 0


# -------------------------------------------------------------------------------------------------------
# reading the HTML
# -------------------------------------------------------------------------------------------------------

def read_embedded(html: str) -> tuple[dict, dict, dict]:
    """(spec, data, slicer_data) from the JSON script tags a report embeds."""
    def tag(name: str) -> dict:
        m = re.search(rf'<script id="{name}" type="application/json">(.*?)</script>', html, re.S)
        return json.loads(m.group(1).replace("<\\/", "</")) if m else {}
    spec = tag("spec")
    if not spec:
        raise ValueError("not a pbix2html report: no embedded spec")
    return spec, tag("data"), tag("slicer-data")


# -------------------------------------------------------------------------------------------------------
# static checks
# -------------------------------------------------------------------------------------------------------

def _box(v: dict) -> tuple[float, float, float, float]:
    return v["left"], v["top"], v["left"] + v["w"], v["top"] + v["h"]


def _is_content(v: dict) -> bool:
    if v.get("kind") in CONTENT_SKIP or v.get("action") or v.get("start_hidden") or v.get("groups"):
        return False
    if v.get("kind") == "text":
        return bool((v.get("text") or "").strip())
    return True


def static_checks(spec: dict, data: dict | None = None, slicer_data: dict | None = None) -> list[Finding]:
    out: list[Finding] = []
    data = data or {}
    for page in spec.get("pages", []):
        pname, W, H = page["name"], page.get("width") or 1280, page.get("height") or 720
        vis = page["visuals"]

        def f(sev, rule, v, msg, hint="", pname=pname):
            out.append(Finding(sev, rule, pname, v.get("id"), msg, hint, v.get("title"), v.get("kind")))

        for v in vis:
            if v.get("start_hidden"):
                continue
            kind = v.get("kind")
            if kind in ("unsupported", "pending", "custom"):
                f("error", "no_renderer", v, f"The visual type “{v.get('type')}” has no renderer (kind {kind}).",
                  "Pick the closest supported kind in the yaml or add a renderer.")
            x0, y0, x1, y1 = _box(v)
            if (x0 < -1 or y0 < -1 or x1 > 101 or y1 > 101) and kind not in CONTENT_SKIP:
                f("warn", "outside_page", v, f"Extends beyond the page (x {x0:.0f}–{x1:.0f}%, y {y0:.0f}–{y1:.0f}%).",
                  "Part of it is not visible; check its position in the original.")
            wpx, hpx = v["w"] * W / 100, v["h"] * H / 100
            if _is_content(v) and (wpx < 24 or hpx < 14):
                f("warn", "too_small", v, f"Only {wpx:.0f}×{hpx:.0f}px on a {W:.0f}×{H:.0f} page.",
                  "Probably a hidden/decorative element or a layout problem; check the original.")
            if kind and kind not in NO_DATA_KINDS and kind in ("card", "kpi", "gauge", "bar", "column", "line", "combo",
                                                               "pie", "table", "matrix", "multicard"):
                block = data.get(v["id"]) if data else None
                if data and block is None:
                    f("warn", "no_data", v, "No data was embedded for this visual.",
                      "Its SQL is a TODO or the query did not run.")
                elif block and block.get("error"):
                    f("error", "data_error", v, f"The query failed: {str(block['error'])[:160]}", "Fix the SQL.")
                elif block and not block.get("rows") and not block.get("skipped"):
                    f("warn", "empty_result", v, "The query returned no rows.",
                      "Check the parameters (year, slicer defaults) and the filters applied to it.")

        content = [v for v in vis if _is_content(v)]
        for i, a in enumerate(content):
            for b in content[i + 1:]:
                ax0, ay0, ax1, ay1 = _box(a)
                bx0, by0, bx1, by1 = _box(b)
                iw, ih = min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0)
                if iw <= 0.3 or ih <= 0.3:
                    continue
                inter = iw * ih
                small = min(a["w"] * a["h"], b["w"] * b["h"]) or 1
                big = max(a["w"] * a["h"], b["w"] * b["h"]) or 1
                frac = inter / small
                if frac < 0.15:
                    continue
                top = a if (a.get("z") or 0) >= (b.get("z") or 0) else b
                under = b if top is a else a
                overlay = frac > 0.85 and small / big < 0.25          # a small control sitting on a bigger visual
                sev = "info" if overlay or frac < 0.4 else "warn"
                msg = (f"“{top.get('title') or top.get('type')}” ({top['id'][:8]}) is drawn over "
                       f"“{under.get('title') or under.get('type')}” ({under['id'][:8]}): {frac * 100:.0f}% of the smaller one overlaps.")
                out.append(Finding(sev, "overlap", pname, top["id"], msg,
                                   "A small control on a chart is normal in Power BI; anything else hides content. "
                                   "Compare with the original.", top.get("title"), top.get("kind")))
    return out


# -------------------------------------------------------------------------------------------------------
# rendered checks
# -------------------------------------------------------------------------------------------------------

ANNOTATE_JS = """
(section, items) => {
  const sr = section.getBoundingClientRect();
  const wrap = document.createElement('div'); wrap.id = '__verify_overlay';
  wrap.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:2147483000';
  const colour = { error: '#e11d48', warn: '#f59e0b', info: '#3b82f6' };
  for (const it of items) {
    if (!it.rect) continue;
    const b = document.createElement('div');
    b.style.cssText = `position:absolute;left:${it.rect[0]}px;top:${it.rect[1]}px;width:${it.rect[2]}px;height:${it.rect[3]}px;`
      + `outline:2px solid ${colour[it.severity]};background:${colour[it.severity]}22`;
    const n = document.createElement('span'); n.textContent = it.n;
    n.style.cssText = `position:absolute;left:-2px;top:-16px;background:${colour[it.severity]};color:#fff;font:700 11px sans-serif;padding:0 4px;border-radius:2px`;
    b.appendChild(n); wrap.appendChild(b);
  }
  if (getComputedStyle(section).position === 'static') section.style.position = 'relative';
  section.appendChild(wrap);
}
"""


# Contrast is measured on pixels, not guessed from CSS: a picture, a gradient or the page's own background image
# has no single colour. The page is screenshotted with every piece of text made transparent, and the pixels
# under each text rectangle are compared with the text colour.
HIDE_TEXT_CSS = """*, *::before, *::after { color: transparent !important; -webkit-text-fill-color: transparent !important;
  text-shadow: none !important; caret-color: transparent !important; }
svg text { fill: transparent !important; } ::placeholder { color: transparent !important; }"""

SAMPLE_JS = """
async ({png, texts}) => {
  const img = new Image(); img.src = 'data:image/png;base64,' + png; await img.decode();
  const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height;
  const cx = cv.getContext('2d', { willReadFrequently: true }); cx.drawImage(img, 0, 0);
  const chan = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (r, g, b) => 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
  return texts.map(t => {
    const [x, y, w, h] = t.rect;
    const X = Math.max(0, x), Y = Math.max(0, y), W = Math.min(cv.width - X, w), H = Math.min(cv.height - Y, h);
    if (W < 2 || H < 2) return null;
    const d = cx.getImageData(X, Y, W, H).data, n = W * H, step = Math.max(1, Math.floor(n / 500));
    const [fr, fg, fb, fa] = t.color, ratios = [], lums = [];
    for (let i = 0; i < n; i += step) {
      const br = d[i * 4], bg = d[i * 4 + 1], bb = d[i * 4 + 2];
      const bl = lum(br, bg, bb);
      const er = fr * fa + br * (1 - fa), eg = fg * fa + bg * (1 - fa), eb = fb * fa + bb * (1 - fa);
      const el = lum(er, eg, eb);
      ratios.push((Math.max(bl, el) + 0.05) / (Math.min(bl, el) + 0.05)); lums.push(bl);
    }
    ratios.sort((a, b) => a - b);
    const mean = lums.reduce((s, v) => s + v, 0) / lums.length;
    const std = Math.sqrt(lums.reduce((s, v) => s + (v - mean) * (v - mean), 0) / lums.length);
    return { median: ratios[Math.floor(ratios.length / 2)], bad: ratios.filter(r => r < 3).length / ratios.length, std };
  });
}
"""


def _contrast_findings(texts: list[dict], stats: list[dict | None], page: str, by_id: dict) -> list[Finding]:
    """One finding per (visual, kind of problem): a table of 40 cells is one problem, not 40."""
    groups: dict[tuple[str, str], list[tuple[dict, dict]]] = {}
    for t, st in zip(texts, stats):
        if not st or t["fs"] < 6:
            continue
        if st["median"] < 1.5:
            rule = "invisible_text"
        elif st["median"] < 3 or st["bad"] > 0.5:
            rule = "low_contrast"
        elif st["bad"] >= 0.25 and st["std"] > 0.12:
            rule = "text_busy_background"
        else:
            continue
        groups.setdefault((t["visual"], rule), []).append((t, st))
    out = []
    for (vid, rule), items in groups.items():
        v = by_id.get(vid, {})
        examples = ", ".join(f"“{t['t'][:24]}”" for t, _ in items[:3]) + (f" and {len(items) - 3} more" if len(items) > 3 else "")
        worst = min(st["median"] for _, st in items)
        xs = [t["rect"] for t, _ in items]
        x0, y0 = min(r[0] for r in xs), min(r[1] for r in xs)
        x1, y1 = max(r[0] + r[2] for r in xs), max(r[1] + r[3] for r in xs)
        if rule == "invisible_text":
            sev, msg, hint = ("error", f"Text the same colour as what is behind it ({worst:.2f}:1): {examples}.",
                              "Power BI usually sets that colour for a dark shape/picture behind it; check the text colour and what is under it.")
        elif rule == "low_contrast":
            sev, msg, hint = "warn", f"Low contrast against the background ({worst:.2f}:1): {examples}.", "Change the text colour or the fill behind it."
        else:
            sev, msg, hint = "info", f"Part of the text sits on a busy background (picture or gradient): {examples}.", "Check it against the original."
        out.append(Finding(sev, rule, page, vid, msg, hint, v.get("title"), v.get("kind"), [x0, y0, x1 - x0, y1 - y0]))
    return out


def _known_browsers() -> list[str]:
    """Where a Chrome / Edge / Chromium usually lives, for machines where Playwright's own download is missing
    (the usual case on a locked-down Windows PC, where Edge is always installed)."""
    import glob
    roots = [os.getenv(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    win = [os.path.join(r, *rest) for r in roots if r for rest in (
        ("Microsoft", "Edge", "Application", "msedge.exe"), ("Google", "Chrome", "Application", "chrome.exe"))]
    pw = os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "/opt/pw-browsers"
    found = sorted(glob.glob(os.path.join(pw, "chromium-*", "chrome-linux", "chrome")))
    posix = ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return [c for c in [*win, *found, *posix] if os.path.exists(c)]


def _launch(p, browser: str | None):
    """A Chromium: an explicit path, then Playwright's own, then an installed Chrome / Edge."""
    errors = []
    path = browser or os.getenv("PBIX2HTML_BROWSER")
    attempts: list[dict] = []
    if path:
        attempts.append({"executable_path": path})
    attempts += [{}, {"channel": "chrome"}, {"channel": "msedge"}]
    attempts += [{"executable_path": c} for c in _known_browsers()]
    for kw in attempts:
        try:
            return p.chromium.launch(**kw), (kw.get("executable_path") or kw.get("channel") or "playwright chromium")
        except Exception as e:                       # noqa: BLE001 - try the next
            errors.append(str(e).splitlines()[0])
    raise RuntimeError("no browser could be started (" + "; ".join(errors[:2]) + "). Install one with "
                       "`playwright install chromium`, or pass --browser <path to chrome/edge>.")


def browser_checks(html_path: Path, spec: dict, out_dir: Path, *, width: int = 1440, echarts: str | None = None,
                   browser: str | None = None, screenshots: bool = True) -> tuple[list[Finding], dict[str, str], str]:
    from playwright.sync_api import sync_playwright

    findings: list[Finding] = []
    shots: dict[str, str] = {}
    by_id = {v["id"]: v for pg in spec["pages"] for v in pg["visuals"]}
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        br, used = _launch(p, browser)
        page = br.new_page(viewport={"width": width, "height": 900})
        js_errors: list[str] = []
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.on("console", lambda m: js_errors.append(m.text) if m.type == "error" else None)
        if echarts:
            src = Path(echarts).read_text(encoding="utf-8") if Path(echarts).exists() else None
            if src is not None:
                page.route(re.compile(r".*echarts.*\.js.*"),
                           lambda route: route.fulfill(body=src, content_type="application/javascript"))
        page.goto(html_path.resolve().as_uri())
        page.wait_for_timeout(1200)
        for i, pg in enumerate(spec["pages"], start=1):
            sel = f"#{pg['id']}"
            page.evaluate("""(id) => {
                document.querySelectorAll('.page').forEach(p => { p.hidden = p.id !== id; });
                document.querySelectorAll('nav.tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.page === id));
                window.dispatchEvent(new Event('resize'));
            }""", pg["id"])
            page.wait_for_timeout(350)
            section = page.locator(sel)
            probe = section.evaluate(PROBE_JS)
            raw, texts = probe["findings"], probe["texts"]
            page_findings: list[Finding] = []
            if texts:
                style = page.add_style_tag(content=HIDE_TEXT_CSS)
                png = section.screenshot(type="png")
                page.evaluate("el => el.remove()", style)
                import base64
                stats = page.evaluate(SAMPLE_JS, {"png": base64.b64encode(png).decode(), "texts": texts})
                page_findings += _contrast_findings(texts, stats, pg["name"], by_id)
            for r in raw:
                v = by_id.get(r.get("visual") or "", {})
                page_findings.append(Finding(r["severity"], r["rule"], pg["name"], r.get("visual"), r["message"],
                                             r.get("hint", ""), v.get("title"), v.get("kind"), r.get("rect")))
            page_findings = _dedupe(page_findings)
            page_findings.sort(key=lambda f: SEVERITIES.index(f.severity))
            for n, f in enumerate(page_findings, start=1):
                f.n = n
            if screenshots and page_findings:
                section.evaluate(ANNOTATE_JS, [{"rect": f.rect, "severity": f.severity, "n": f.n} for f in page_findings])
                name = f"page-{i}.png"
                section.screenshot(path=str(out_dir / name))
                page.evaluate("document.getElementById('__verify_overlay')?.remove()")
                shots[pg["name"]] = name
            findings += page_findings
        if js_errors:
            for msg in dict.fromkeys(js_errors):
                findings.append(Finding("error", "js_error", "(report)", None, f"Browser console error: {msg[:200]}",
                                        "A failing script leaves visuals blank."))
        br.close()
    return findings, shots, used


def _dedupe(items: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in items:
        key = (f.rule, f.visual, f.message)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


# -------------------------------------------------------------------------------------------------------
# orchestration and report
# -------------------------------------------------------------------------------------------------------

# What HAH's upload validator refuses (reported by a real HAH, 2026-10-02): dynamic code, and
# scripts it does not recognise — those it removes, which would leave the report without the code
# that draws it. Checked here because it is cheaper to read than an upload that silently loses
# half the file. The "known" CDNs are the ones HAH rewrites to its own copies instead of removing.
_HAH_DYNAMIC = re.compile(r"(?<![\w$.])(?:new\s+Function|Function|eval)\s*\(")
_HAH_KNOWN_LIBS = ("chart.js", "plotly", "mermaid")


def hah_upload_checks(html: str) -> list[Finding]:
    """What HAH's validator would object to in this file (ADR-004). Only meaningful for hah mode."""
    out: list[Finding] = []

    def f(sev, rule, msg, hint=""):
        out.append(Finding(sev, rule, "(report)", None, msg, hint))

    for m in _HAH_DYNAMIC.finditer(html):
        line = html.count("\n", 0, m.start()) + 1
        f("error", "hah_dynamic_code",
          f"`{m.group(0).strip()}` on line {line}: HAH's validator disallows the dynamic Function() "
          f"constructor and eval, and removes the script that uses them.",
          "Rewrite it without dynamic code; a library that needs it cannot be embedded.")
        break                       # one finding is the point; the file is either clean or not
    for m in re.finditer(r"<script[^>]*\bsrc=[\"']([^\"']+)[\"']", html, re.I):
        url = m.group(1)
        if url.startswith(("http://", "https://", "//")) and not any(k in url.lower() for k in _HAH_KNOWN_LIBS):
            f("error", "hah_unknown_script",
              f"External script {url}: HAH only rewrites Chart.js, Plotly and Mermaid to its own "
              f"copies — anything else it removes.",
              "Embed the library instead (`--echarts download`, the default for --mode hah).")
    # `b.document.write(...)` into a window.open() is ECharts' save-as-image, not this document
    if re.search(r"(?<![\w$.])document\.write\s*\(", html):
        f("warn", "hah_document_write", "`document.write` runs while the page parses; a validator "
          "that rewrites scripts can break it.", "Build the element instead.")
    return out


def verify_html(html_path: Path, out_dir: Path | None = None, *, static_only: bool = False, width: int = 1440,
                echarts: str | None = None, browser: str | None = None, screenshots: bool = True) -> VerifyResult:
    html_path = Path(html_path)
    out_dir = Path(out_dir) if out_dir else html_path.parent / f"{html_path.stem}.verify"
    text = html_path.read_text(encoding="utf-8")
    spec, data, slicer_data = read_embedded(text)
    res = VerifyResult(pages=len(spec["pages"]))
    res.findings = static_checks(spec, data, slicer_data)
    if "__PBIX2HTML_SQL_API__" in text:          # a hah file, whatever it was asked to check
        res.findings += hah_upload_checks(text)
    if not static_only:
        try:
            found, shots, used = browser_checks(html_path, spec, out_dir, width=width, echarts=echarts,
                                                browser=browser, screenshots=screenshots)
            res.findings += found
            res.screenshots, res.browser = shots, used
        except ImportError:
            res.browser = "skipped (playwright isn't installed: pip install 'pbix2html[verify]')"
        except RuntimeError as e:
            res.browser = f"skipped ({e})"
    res.findings = _dedupe(res.findings)
    order = {p["name"]: i for i, p in enumerate(spec["pages"])}
    res.findings.sort(key=lambda f: (order.get(f.page, 999), SEVERITIES.index(f.severity), f.n))
    out_dir.mkdir(parents=True, exist_ok=True)
    res.report = out_dir / "verify_report.md"
    res.report.write_text(render_report(res, spec.get("report") or html_path.stem), encoding="utf-8")
    (out_dir / "verify_report.json").write_text(
        json.dumps({"report": spec.get("report"), "browser": res.browser, "findings": [asdict(f) for f in res.findings]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def verify_after_convert(html_path: Path, mode: str) -> tuple[list[str], VerifyResult | None]:
    """The check `convert` runs on every HTML it writes (ADR-009): the lines to show the user, and the result.
    A `live` or `hah` page has no data until its service answers, so only the static checks run there (a
    rendered run would report every visual as failed). Never raises: a verifier problem must not lose the HTML."""
    try:
        res = verify_html(html_path, static_only=mode != "snapshot")
    except Exception as e:  # noqa: BLE001
        return [f"HTML check couldn't run: {type(e).__name__}: {e}"], None
    head = (f"HTML check: {res.count('error')} error(s), {res.count('warn')} warning(s), {res.count('info')} note(s)"
            f" [rendered checks: {res.browser or 'not run for ' + mode + ' mode'}]")
    lines = [head] + [f"  {f.severity.upper():5} {f.page} / {f.title or f.kind or ''} [{f.rule}] {f.message}"
                      for f in res.findings if f.severity != "info"][:15]
    lines.append(f"  report: {res.report}")
    return lines, res


_ICON = {"error": "🔴", "warn": "🟠", "info": "🔵"}


def render_report(res: VerifyResult, name: str) -> str:
    L = [f"# HTML verification: {name}", "",
         f"- Pages: **{res.pages}**, findings: **{res.count('error')} errors**, {res.count('warn')} warnings, "
         f"{res.count('info')} notes",
         f"- Rendered checks: {res.browser}", ""]
    if not res.findings:
        L += ["No problems found by these checks. This does **not** prove the report matches the Power BI "
              "original: compare it side by side.", ""]
        return "\n".join(L)
    L += ["Findings are things that are objectively wrong or suspicious in the HTML; whether each one also happens "
          "in the Power BI original is for a person to confirm (an overlay a designer meant is a note, not a bug).", ""]
    pages: dict[str, list[Finding]] = {}
    for f in res.findings:
        pages.setdefault(f.page, []).append(f)
    for page, items in pages.items():
        L += [f"## {page}", ""]
        if page in res.screenshots:
            L += [f"![{page}]({res.screenshots[page]})", ""]
        L += ["| # | | Rule | Visual | What | Suggestion |", "|--:|:-:|---|---|---|---|"]
        for f in items:
            vis = f"{f.title or f.kind or ''} `{(f.visual or '')[:8]}`".strip()
            L += [f"| {f.n or ''} | {_ICON[f.severity]} | `{f.rule}` | {vis} | {_md(f.message)} | {_md(f.hint)} |"]
        L += [""]
    return "\n".join(L)


def _md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")
