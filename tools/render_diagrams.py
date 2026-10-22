"""Рендер Mermaid-диаграмм из документации в PNG и SVG (docs/diagrams/).

Источник диаграмм — блоки ```mermaid в docs/architecture.md, docs/state-diagram.md, docs/data-model.md;
GitHub показывает их и так, а PNG нужны для просмотра вне GitHub.

    npm install mermaid@11            # в любой временной папке
    pip install playwright && playwright install chromium
    python tools/render_diagrams.py --mermaid-js /path/to/node_modules/mermaid/dist/mermaid.min.js
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path

from playwright.async_api import async_playwright

DOCS = Path(__file__).resolve().parent.parent / "docs"
SOURCES = {
    "architecture": DOCS / "architecture.md",
    "state-diagram": DOCS / "state-diagram.md",
    "data-model": DOCS / "data-model.md",
}

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#fff;font-family:system-ui,sans-serif} #d{padding:24px;display:inline-block}</style>
</head><body><div id="d"></div><script>__MERMAID_JS__</script><script>
mermaid.initialize({startOnLoad:false, theme:"base", themeVariables:{
  primaryColor:"#ffffff", primaryBorderColor:"#141c2b", primaryTextColor:"#141c2b",
  lineColor:"#4a5468", secondaryColor:"#fff4cf", tertiaryColor:"#f3f5f8", fontSize:"15px"
}, flowchart:{curve:"basis"}});
window.render = async (code) => {
  const { svg } = await mermaid.render("g", code);
  document.getElementById("d").innerHTML = svg;
  // mermaid вписывает схему в ширину контейнера — задаём натуральный размер с запасом
  const el = document.querySelector("#d svg");
  const vb = el.viewBox.baseVal;
  el.removeAttribute("style");
  el.setAttribute("width", Math.round(vb.width * 1.0));
  el.setAttribute("height", Math.round(vb.height * 1.0));
  return svg;
};
</script></body></html>"""


def extract(md: Path) -> str:
    m = re.search(r"```mermaid\n(.*?)```", md.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit(f"Нет блока mermaid в {md}")
    return m.group(1)


async def main(mermaid_js: Path) -> None:
    out = DOCS / "diagrams"
    out.mkdir(exist_ok=True)
    html = PAGE.replace("__MERMAID_JS__", mermaid_js.read_text(encoding="utf-8"))
    async with async_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        browser = await p.chromium.launch(executable_path=exe) if exe else await p.chromium.launch()
        page = await browser.new_page(device_scale_factor=2, viewport={"width": 2400, "height": 1600})
        await page.set_content(html)
        for name, src in SOURCES.items():
            svg = await page.evaluate("code => window.render(code)", extract(src))
            (out / f"{name}.svg").write_text(svg, encoding="utf-8")
            await page.locator("#d").screenshot(path=str(out / f"{name}.png"))
            print(f"  {name}.png / {name}.svg")
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mermaid-js", required=True, type=Path)
    asyncio.run(main(parser.parse_args().mermaid_js))
