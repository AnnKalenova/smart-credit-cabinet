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

# Палитра интерфейса: текст и стрелки — ink #212124, фон страницы #F2F3F5, белые карточки с рамкой #D5D6DC,
# акцент #EF3124 (текст на белом — #D72505), зелёный #0B7A2E — в classDef диаграммы состояний
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#F2F3F5;font-family:system-ui,sans-serif} #d{padding:24px;display:inline-block}</style>
</head><body><div id="d"></div><script>__MERMAID_JS__</script><script>
mermaid.initialize({startOnLoad:false, theme:"base", themeVariables:{
  background:"#F2F3F5", fontSize:"15px",
  primaryColor:"#FFFFFF", primaryTextColor:"#212124", primaryBorderColor:"#D5D6DC",
  secondaryColor:"#FFFFFF", tertiaryColor:"#FFFFFF",
  lineColor:"#212124", textColor:"#212124",
  edgeLabelBackground:"#F2F3F5", labelBackgroundColor:"#F2F3F5",
  clusterBkg:"#FFFFFF", clusterBorder:"#D5D6DC", titleColor:"#D72505",
  noteBkgColor:"#FFFFFF", noteBorderColor:"#EF3124", noteTextColor:"#212124",
  attributeBackgroundColorOdd:"#FFFFFF", attributeBackgroundColorEven:"#F2F3F5"
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
