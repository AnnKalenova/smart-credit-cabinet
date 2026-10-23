"""Картинки для README и страницы репозитория: анимация конструктора условий и превью для соцсетей.

Анимация снимается с работающего стенда: ИП Кузнецова получает встречное предложение, затем в конструкторе
выбирается срок из предложения банка и включается поручительство собственника. Превью 1280×640 собирается
из HTML с фирменными цветами кабинета и снимком конструктора.

    pip install playwright httpx pillow
    python tools/render_readme_media.py --base-url http://localhost:8080

Результат: docs/screenshots/conditions-builder.gif и docs/social-preview.png.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import os
import sys
from pathlib import Path

import httpx
from PIL import Image
from playwright.async_api import Locator, Page, async_playwright

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "e2e"))
from api_scenarios import Client, prepare, submit_and_wait  # noqa: E402

GIF = REPO / "docs" / "screenshots" / "conditions-builder.gif"
PREVIEW = REPO / "docs" / "social-preview.png"
FONT = REPO / "frontend" / "node_modules" / "@fontsource-variable" / "golos-text" / "files"
GIF_WIDTH = 640


async def fresh(builder: Locator, page: Page) -> None:
    """Ждём, пока конструктор пересчитает решение для выбранных условий."""
    await page.wait_for_timeout(450)
    await builder.locator(".verdict.stale").wait_for(state="detached", timeout=15_000)
    await page.wait_for_timeout(250)


async def frame(builder: Locator) -> Image.Image:
    png = await builder.screenshot()
    img = Image.open(io.BytesIO(png)).convert("RGB")
    return img.resize((GIF_WIDTH, round(img.height * GIF_WIDTH / img.width)), Image.LANCZOS)


async def record_builder(page: Page, base: str) -> list[tuple[Image.Image, int]]:
    c = Client(base, "kuznetsova")
    app = prepare(c, "EXPRESS", "EXPANSION", 1_000_000, 12, purpose_details="Ремонт второго зала")
    submit_and_wait(c, app["id"])

    await page.goto(f"{base}/login")
    await page.get_by_role("button", name="Кузнецова Анна Сергеевна").first.click()
    await page.wait_for_url(lambda url: "/c" in url)
    await page.goto(f"{base}/c/applications/{app['id']}")
    await page.add_style_tag(content=".topbar { position: static !important; } .toast { display: none !important; }")
    builder = page.locator("section", has=page.get_by_role("heading", name="Конструктор условий"))
    await builder.locator(".verdict-title").wait_for()
    await builder.scroll_into_view_if_needed()
    await fresh(builder, page)

    frames: list[tuple[Image.Image, int]] = [(await frame(builder), 2200)]  # встречное предложение
    await builder.get_by_role("radio", name="18").click()
    frames.append((await frame(builder), 350))  # пересчитываем
    await fresh(builder, page)
    frames.append((await frame(builder), 2300))  # вариант из предложения банка
    await builder.get_by_text("Поручительство собственника").first.click()
    frames.append((await frame(builder), 350))
    await fresh(builder, page)
    frames.append((await frame(builder), 3200))  # одобрим автоматически, ставка ниже
    amount = builder.locator("#builder-amount")
    await amount.fill("1500000")
    await amount.blur()
    frames.append((await frame(builder), 350))
    await fresh(builder, page)
    frames.append((await frame(builder), 2400))  # больше сумма — снова другие условия
    return frames


def save_gif(frames: list[tuple[Image.Image, int]]) -> None:
    # кадры разной высоты (подсказки появляются и исчезают) — выравниваем по самому высокому
    height = max(img.height for img, _ in frames)
    canvas = []
    for img, _ in frames:
        bg = Image.new("RGB", (GIF_WIDTH, height), (242, 243, 245))
        bg.paste(img, (0, 0))
        canvas.append(bg.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    canvas[0].save(
        GIF,
        save_all=True,
        append_images=canvas[1:],
        duration=[d for _, d in frames],
        loop=0,
        optimize=True,
        disposal=1,
    )
    print(f"  {GIF.relative_to(REPO)}: {len(frames)} кадров, {GIF.stat().st_size // 1024} КБ")


def font_face() -> str:
    faces = []
    for name in ("golos-text-cyrillic-wght-normal.woff2", "golos-text-latin-wght-normal.woff2"):
        path = FONT / name
        if path.exists():
            data = base64.b64encode(path.read_bytes()).decode()
            faces.append(
                "@font-face { font-family: 'Golos Text'; font-weight: 400 900; "
                f"src: url(data:font/woff2;base64,{data}) format('woff2'); }}"
            )
    return "\n".join(faces)


def preview_html(shot_b64: str) -> str:
    stations = [
        ("Заявка", "done"),
        ("Проверка и оценка", "done"),
        ("Предложение", "current"),
        ("Подписание", ""),
        ("Выдача", ""),
    ]
    track = "".join(f'<div class="st {cls}"><i></i><span>{name}</span></div>' for name, cls in stations)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{font_face()}
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1280px; height: 640px; overflow: hidden; background: #f2f3f5; color: #212124;
  font-family: 'Golos Text', system-ui, sans-serif; position: relative; }}
.left {{ position: absolute; left: 72px; top: 64px; width: 640px; }}
.brand {{ display: flex; align-items: center; gap: 16px; font-weight: 700; font-size: 22px; }}
.mark {{ width: 52px; height: 52px; border-radius: 15px; background: #ef3124; position: relative; }}
.mark span, .mark span::before, .mark span::after {{ position: absolute; left: 11px; height: 6px; border-radius: 3px;
  background: #fff; content: ""; }}
.mark span {{ top: 23px; width: 30px; }}
.mark span::before {{ top: -11px; left: 0; width: 18px; }}
.mark span::after {{ top: 11px; left: 0; width: 24px; }}
.brand small {{ display: block; font-size: 15px; font-weight: 500; color: #5c5c66; margin-top: 2px; }}
h1 {{ font-size: 54px; line-height: 1.05; letter-spacing: -0.035em; font-weight: 800; margin-top: 36px; }}
p {{ font-size: 21px; line-height: 1.42; color: #5c5c66; margin-top: 18px; max-width: 540px; }}
.band {{ position: absolute; left: 72px; bottom: 52px; width: 640px; height: 104px; border-radius: 26px;
  background: #212124; padding: 24px 16px 0; display: flex; justify-content: space-between; }}
.band::before {{ content: ""; position: absolute; left: 76px; right: 76px; top: 39px; border-top: 2px dashed rgba(255,255,255,.25); }}
.st {{ position: relative; display: flex; flex-direction: column; align-items: center; gap: 12px; width: 120px;
  color: rgba(255,255,255,.62); font-size: 14px; text-align: center; }}
.st i {{ width: 30px; height: 30px; border-radius: 50%; background: #212124; border: 2px solid rgba(255,255,255,.35); }}
.st.done i {{ background: #fff; border-color: #fff; }}
.st.current {{ color: #fff; font-weight: 600; }}
.st.current i {{ background: #ef3124; border-color: #ef3124; box-shadow: 0 0 0 7px rgba(239,49,36,.3); }}
.shot {{ position: absolute; right: 64px; top: 56px; width: 452px; border-radius: 24px; overflow: hidden;
  box-shadow: 0 30px 60px rgba(15,25,55,.16), 0 0 0 1px rgba(15,25,55,.06); background: #fff; }}
.shot img {{ display: block; width: 100%; }}
</style></head><body>
<div class="left">
  <div class="brand"><div class="mark"><span></span></div><div>Кредитный кабинет<small>для малого бизнеса</small></div></div>
  <h1>Умный кредитный кабинет предпринимателя</h1>
  <p>От заявки до денег на счёте за минуты. Конструктор условий показывает, на чём банк скажет «да».</p>
</div>
<div class="band">{track}</div>
<div class="shot"><img src="data:image/png;base64,{shot_b64}"></div>
</body></html>"""


async def render_preview(browser) -> None:
    shot = REPO / "docs" / "screenshots" / "19-conditions-builder.png"
    page = await browser.new_page(viewport={"width": 1280, "height": 640}, device_scale_factor=1)
    await page.set_content(preview_html(base64.b64encode(shot.read_bytes()).decode()))
    await page.wait_for_timeout(400)
    await page.screenshot(path=str(PREVIEW))
    await page.close()
    print(f"  {PREVIEW.relative_to(REPO)}: 1280×640")


async def main(base: str) -> None:
    async with async_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        browser = await p.chromium.launch(executable_path=exe) if exe else await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1360, "height": 1000}, locale="ru-RU", device_scale_factor=1)
        save_gif(await record_builder(page, base))
        await render_preview(browser)
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    args = parser.parse_args()
    httpx.get(f"{args.base_url}/health/ready", timeout=10).raise_for_status()
    asyncio.run(main(args.base_url))
