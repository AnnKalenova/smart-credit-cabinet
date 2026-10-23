"""Проверки интерфейса в браузере (Playwright) — то, что не увидеть через API.

1. Автосохранение черновика не прошло (HTTP 503): правка остаётся в форме, заявка не уходит в банк со старой
   суммой, а когда связь восстановилась, уходит с суммой из формы.
2. Автосохранение идёт медленно: отправка ждёт, пока правка дойдёт до банка, и не обгоняет её.
3. Клиент изменил сумму и сразу ушёл из черновика или вышел из кабинета: правка всё равно сохраняется.

    pip install playwright httpx && python -m playwright install chromium
    python e2e/ui_checks.py --base-url http://localhost:8080

Скрипт возвращает код 1, если хоть одна проверка не прошла. Путь к своему Chromium — в CHROMIUM_PATH.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

import httpx
from playwright.async_api import Page, Route, async_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api_scenarios import Client, prepare  # noqa: E402

DIRECTOR = "Смирнов Алексей Викторович"


async def open_draft(page: Page, base: str, app_id: str) -> None:
    await page.goto(f"{base}/login")
    await page.get_by_role("button", name=DIRECTOR).first.click()
    await page.wait_for_url(lambda url: "/c" in url)
    await page.goto(f"{base}/c/applications/{app_id}")
    await page.get_by_role("heading", name="Параметры кредита").wait_for()


def new_draft(base: str) -> tuple[Client, dict]:
    c = Client(base, "smirnov")
    return c, prepare(c, "EXPRESS", "OTHER", 300_000, 6, purpose_details="Проверка автосохранения")


def wait_for(c: Client, url: str, ok, timeout: float = 15) -> dict:
    """Заявка по API, как только выполнено условие (ждём не дольше timeout секунд)."""
    deadline = time.monotonic() + timeout
    while True:
        app = c.req("GET", url)
        if ok(app) or time.monotonic() > deadline:
            return app
        time.sleep(0.3)


async def check_failed_autosave(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    submits: list[str] = []
    page.on("request", lambda r: submits.append(r.method) if r.url.endswith(f"{url}/submit") else None)

    async def unavailable(route: Route) -> None:
        if route.request.method == "PATCH":
            await route.fulfill(status=503, json={"detail": "Сервис временно недоступен"})
        else:
            await route.continue_()

    await open_draft(page, base, app["id"])
    await page.route(f"**{url}", unavailable)
    amount = page.get_by_label("Сумма")
    await amount.fill("700000")
    await expect(page.get_by_text("Изменения не сохранены")).to_be_visible(timeout=10_000)

    await page.get_by_role("button", name="Отправить заявку").click()
    await expect(page.get_by_text("Заявка не отправлена: не удалось сохранить изменения")).to_be_visible()
    await expect(amount).to_have_value(re.compile(r"^700\s000$"))  # разряды — неразрывным пробелом
    saved = c.req("GET", url)
    assert submits == [], "заявка ушла в банк, хотя правка не сохранилась"
    assert saved["status"] == "DRAFT" and saved["amount"] == 300_000, (saved["status"], saved["amount"])

    await page.unroute(f"**{url}", unavailable)  # связь восстановилась
    await page.get_by_role("button", name="Отправить заявку").click()
    sent = await asyncio.to_thread(wait_for, c, url, lambda a: a["status"] != "DRAFT")
    assert sent["status"] != "DRAFT" and sent["amount"] == 700_000, (sent["status"], sent["amount"])
    return "при HTTP 503 правка осталась в форме, заявка не ушла; после восстановления ушла с 700 000 ₽"


async def check_slow_autosave(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    moments: dict[str, float] = {}

    async def slow(route: Route) -> None:
        if route.request.method == "PATCH":
            await asyncio.sleep(2.5)
        await route.continue_()

    def on_request(r) -> None:
        if r.url.endswith(f"{url}/submit"):
            moments["submit_sent"] = time.monotonic()

    def on_response(r) -> None:
        if r.request.method == "PATCH" and r.url.endswith(url):
            moments["patch_done"] = time.monotonic()

    page.on("request", on_request)
    page.on("response", on_response)
    await open_draft(page, base, app["id"])
    await page.route(f"**{url}", slow)
    await page.get_by_label("Сумма").fill("650000")
    await page.wait_for_timeout(900)  # автосохранение уже ушло и ещё не вернулось
    await page.get_by_role("button", name="Отправить заявку").click()
    sent = await asyncio.to_thread(wait_for, c, url, lambda a: a["status"] != "DRAFT", 20)
    assert sent["status"] != "DRAFT", "заявка не отправлена"
    assert sent["amount"] == 650_000, f"в банк ушла сумма {sent['amount']}"
    assert moments["submit_sent"] >= moments["patch_done"], "отправка обогнала сохранение"
    return "отправка дождалась медленного сохранения: в банк ушло 650 000 ₽"


async def check_leave_right_after_edit(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    await open_draft(page, base, app["id"])
    await page.get_by_label("Сумма").fill("450000")
    await page.get_by_role("link", name="Главная").first.click()  # раньше, чем сработало автосохранение
    await page.wait_for_url(lambda u: u.rstrip("/").endswith("/c"))
    saved = await asyncio.to_thread(wait_for, c, url, lambda a: a["amount"] == 450_000, 5)
    assert saved["amount"] == 450_000, f"правка потерялась: в банке {saved['amount']}"
    return "сумма 450 000 ₽ сохранилась, хотя клиент сразу ушёл на главную"


async def check_logout_right_after_edit(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    await open_draft(page, base, app["id"])
    await page.get_by_label("Сумма").fill("550000")
    await page.get_by_label("Выйти").click()  # раньше, чем сработало автосохранение
    await page.wait_for_url("**/login")
    saved = await asyncio.to_thread(wait_for, c, url, lambda a: a["amount"] == 550_000, 5)
    assert saved["amount"] == 550_000, f"правка потерялась: в банке {saved['amount']}"
    return "сумма 550 000 ₽ сохранилась, хотя клиент сразу вышел из кабинета"


CHECKS = [
    ("Ошибка автосохранения не отправляет старую сумму", check_failed_autosave),
    ("Отправка ждёт медленного автосохранения", check_slow_autosave),
    ("Правка сохраняется при уходе из черновика", check_leave_right_after_edit),
    ("Правка сохраняется при выходе из кабинета", check_logout_right_after_edit),
]


async def run(base: str) -> int:
    failed = 0
    async with async_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH")
        browser = await (p.chromium.launch(executable_path=exe) if exe else p.chromium.launch())
        for title, check in CHECKS:
            context = await browser.new_context(locale="ru-RU", viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            started = time.time()
            try:
                result = await check(page, base)
                print(f"✅ {title} ({time.time() - started:.1f} с): {result}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"❌ {title}: {e}")
            finally:
                await context.close()
        await browser.close()
    print(f"\nИтого: {len(CHECKS) - failed} из {len(CHECKS)} проверок прошли")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    args = parser.parse_args()
    httpx.get(f"{args.base_url}/health/ready", timeout=10).raise_for_status()
    return asyncio.run(run(args.base_url))


if __name__ == "__main__":
    sys.exit(main())
