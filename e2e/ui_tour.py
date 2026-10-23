"""UI-прогон основного сценария в браузере (Playwright) со скриншотами каждого шага.

Проходит путь предпринимателя от подбора продукта до выдачи, конструктор условий
и путь аналитика по заявке на ручном рассмотрении. Скриншоты сохраняются в docs/screenshots/.

    pip install playwright httpx && playwright install chromium
    python e2e/ui_tour.py --base-url http://localhost:8080
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
from playwright.async_api import Page, async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_scenarios import Client, prepare, submit_and_wait  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


async def shot(page: Page, name: str, full: bool = True) -> None:
    await page.wait_for_timeout(600)
    if full:
        # иначе «липкая» шапка окажется посреди полноразмерного скриншота
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
    await page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print(f"  📸 {name}")


async def login(page: Page, base: str, who: str) -> None:
    await page.goto(f"{base}/login")
    await page.get_by_role("button", name=who).first.click()
    await page.wait_for_url(lambda url: "/c" in url or "/b" in url)
    await page.wait_for_timeout(800)


async def logout(page: Page) -> None:
    await page.get_by_label("Выйти").click()
    await page.wait_for_url("**/login")


async def client_journey(page: Page, base: str) -> None:
    print("Путь предпринимателя (ООО «Северный хлеб»)")
    await page.goto(f"{base}/login")
    await page.locator(".login-sample").wait_for()
    await page.wait_for_timeout(1200)  # пока путь заявки на панели входа дойдёт до решения
    await shot(page, "01-login", full=False)  # первый экран: панель входа не прокручивается
    await login(page, base, "Смирнов Алексей Викторович")
    await shot(page, "02-client-home")

    await page.get_by_role("link", name="Подобрать кредит").first.click()
    await page.get_by_role("button", name="Закупка товара, сырья, материалов").click()
    await page.get_by_label("Сумма").fill("3000000")
    await page.get_by_label("Срок").select_option("12")
    await page.wait_for_selector("text=Подходящие варианты")
    await shot(page, "03-product-match")

    await page.locator("article.option", has_text="Оборотный кредит").get_by_role("button", name="Оформить").click()
    await page.wait_for_url("**/c/applications/**")
    await page.get_by_role("heading", name="Параметры кредита").wait_for()
    await page.get_by_label("Коротко о задаче").fill("Закупка муки и сахара перед новогодним сезоном")
    await page.wait_for_timeout(1200)
    await shot(page, "04-application-draft")

    await page.get_by_role("button", name="Подписать согласия").click()
    await page.get_by_role("button", name="Вставить код").click()
    await shot(page, "05-sign-consents", full=False)
    await page.get_by_role("button", name="Подписать", exact=True).click()
    await page.get_by_role("button", name="Отправить заявку").click()
    await page.wait_for_selector("text=Что проверяет банк")
    await page.wait_for_timeout(1500)
    await shot(page, "06-application-processing", full=False)

    await page.wait_for_selector("text=Предложение банка", timeout=60_000)
    await shot(page, "07-offer")

    await page.get_by_role("button", name="Выбрать этот вариант").first.click()
    await page.wait_for_selector("text=Документы к подписанию")
    await page.get_by_role("button", name="Посмотреть договор и график").click()
    await shot(page, "08-contract", full=False)
    await page.keyboard.press("Escape")
    await page.get_by_role("button", name="Подписать кодом").click()
    await page.get_by_role("button", name="Вставить код").click()
    await page.get_by_role("button", name="Подписать", exact=True).click()
    await page.wait_for_selector("text=Деньги на расчётном счёте", timeout=60_000)
    await shot(page, "09-issued")
    await page.get_by_role("link", name="Открыть график платежей").click()
    await page.wait_for_selector("text=График платежей")
    await shot(page, "10-loan-schedule")
    await logout(page)


async def counter_offer_view(page: Page, base: str) -> None:
    print("Встречное предложение (ИП Кузнецова)")
    c = Client(base, "kuznetsova")
    app = prepare(c, "EXPRESS", "EXPANSION", 1_000_000, 12, purpose_details="Ремонт второго зала")
    submit_and_wait(c, app["id"])
    await login(page, base, "Кузнецова Анна Сергеевна")
    await page.goto(f"{base}/c/applications/{app['id']}")
    await page.wait_for_selector("text=Выберите вариант")
    await shot(page, "11-counter-offer")

    # конструктор условий: срок из предложения банка и поручительство собственника — одобрение со ставкой
    # ниже, чем в предложении (сам вариант банка конструктор узнаёт и предлагает просто выбрать)
    builder = page.locator("section", has=page.get_by_role("heading", name="Конструктор условий"))
    await builder.locator(".verdict-title").wait_for()
    await builder.get_by_role("radio", name="18").click()
    await builder.get_by_text("Выбрать этот вариант").wait_for()
    await builder.get_by_text("Поручительство собственника").first.click()
    # пересчёт готов, когда кнопка подачи новой заявки стала доступна
    await page.wait_for_function(
        """() => [...document.querySelectorAll('button')].some(
            (b) => b.textContent.includes('Подать заявку на этих условиях') && !b.disabled)""",
        timeout=15_000,
    )
    await page.wait_for_timeout(500)
    # «липкая» шапка не должна перекрывать снимок блока
    sticky = await page.add_style_tag(content=".topbar { position: static !important; }")
    await builder.screenshot(path=str(OUT / "19-conditions-builder.png"))
    await sticky.evaluate("el => el.remove()")
    print("  📸 19-conditions-builder")

    # та же заявка на телефоне
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(800)
    await page.screenshot(path=str(OUT / "20-mobile-application.png"))
    print("  📸 20-mobile-application")
    await page.set_viewport_size({"width": 1360, "height": 900})
    await logout(page)


async def needs_info_view(page: Page, base: str) -> None:
    print("Запрос уточнений (ИП Морозов)")
    c = Client(base, "morozov")
    app = prepare(c, "OVERDRAFT", "CASH_GAP", 350_000, 12, declared_revenue=14_400_000)
    submit_and_wait(c, app["id"])
    await login(page, base, "Морозов Дмитрий Игоревич")
    await page.goto(f"{base}/c/applications/{app['id']}")
    await page.wait_for_selector("text=Банку нужны уточнения")
    await shot(page, "12-needs-info")
    await logout(page)


async def analyst_journey(page: Page, base: str) -> None:
    print("Путь аналитика (ООО «ТехноМаш НН»)")
    c = Client(base, "lebedev")
    app = prepare(
        c,
        "INVESTMENT",
        "EQUIPMENT",
        25_000_000,
        60,
        docs=["SUPPLIER_INVOICE", "FIN_STATEMENTS"],
        collateral_description="Обрабатывающий центр с ЧПУ, стоимость 31 млн ₽",
        purpose_details="Покупка обрабатывающего центра с ЧПУ",
    )
    submit_and_wait(c, app["id"])
    decl = Client(base, "fedorov")
    d_app = prepare(decl, "EXPRESS", "WORKING_CAPITAL", 2_000_000, 24)
    submit_and_wait(decl, d_app["id"])

    await login(page, base, "Волкова Ирина Андреевна")
    await shot(page, "13-bank-queue")
    await page.goto(f"{base}/b/applications/{app['id']}")
    await page.wait_for_selector("text=Почему заявка у специалиста")
    await shot(page, "14-bank-dossier")
    await page.get_by_role("tab", name="Скоринг").click()
    await shot(page, "15-bank-scoring")
    await page.get_by_role("tab", name="Источники").click()
    await shot(page, "16-bank-sources")
    await page.get_by_role("button", name="Отправить предложение").click()
    await page.wait_for_selector("text=Решение принято")
    await page.goto(f"{base}/b/dashboard")
    await page.wait_for_selector("text=Мониторинг конвейера")
    await shot(page, "17-bank-dashboard")
    await page.goto(f"{base}/b/settings")
    await shot(page, "18-bank-settings")
    await logout(page)


async def main(base: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        browser = await p.chromium.launch(executable_path=exe) if exe else await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1360, "height": 900}, locale="ru-RU")
        await client_journey(page, base)
        await counter_offer_view(page, base)
        await needs_info_view(page, base)
        await analyst_journey(page, base)
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    args = parser.parse_args()
    httpx.get(f"{args.base_url}/health/ready", timeout=10).raise_for_status()
    asyncio.run(main(args.base_url))
