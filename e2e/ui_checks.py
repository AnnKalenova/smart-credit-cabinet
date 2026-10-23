"""Проверки интерфейса в браузере (Playwright) — то, что не увидеть через API.

1. Автосохранение черновика не прошло (HTTP 503): правка остаётся в форме, заявка не уходит в банк со старой
   суммой, а когда связь восстановилась, уходит с суммой из формы.
2. Автосохранение идёт медленно: отправка ждёт, пока правка дойдёт до банка, и не обгоняет её.
3. Клиент изменил сумму и сразу ушёл из черновика или вышел из кабинета: правка всё равно сохраняется.
4. При сбое сохранения переход, «Назад» и выход отменяются; после восстановления связи правка сохраняется.
5. Медленный запрос не теряется при выходе, а перезагрузка предупреждает о несохранённых изменениях.
6. Вёрстка ключевых экранов клиента и банка не уезжает за край окна ни на телефоне, ни на широком мониторе.

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


async def fail_patches(route: Route) -> None:
    if route.request.method == "PATCH":
        await route.fulfill(status=503, json={"detail": "Сервис временно недоступен"})
    else:
        await route.continue_()


async def check_navigation_during_failure(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    await open_draft(page, base, app["id"])
    await page.route(f"**{url}", fail_patches)
    await page.get_by_label("Сумма").fill("870000")
    await expect(page.get_by_text("Изменения не сохранены")).to_be_visible()
    await page.get_by_role("link", name="Главная", exact=True).first.click()
    await expect(page.get_by_text("Переход отменён", exact=False)).to_be_visible()
    await expect(page).to_have_url(f"{base}/c/applications/{app['id']}")
    await expect(page.get_by_label("Сумма")).to_have_value(re.compile(r"^870\s000$"))
    assert c.req("GET", url)["amount"] == 300_000
    await page.unroute(f"**{url}", fail_patches)
    await page.get_by_role("link", name="Главная", exact=True).first.click()
    await expect(page).to_have_url(f"{base}/c")
    assert c.req("GET", url)["amount"] == 870_000
    await page.goto(f"{base}/c/applications/{app['id']}")
    await expect(page.get_by_label("Сумма")).to_have_value(re.compile(r"^870\s000$"))
    return "при HTTP 503 переход отменён; после восстановления сохранены 870 000 ₽"


async def check_back_during_failure(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    await open_draft(page, base, app["id"])
    # Обе записи истории созданы маршрутизатором: проверяем именно браузерный POP-переход.
    await page.get_by_role("link", name="Главная", exact=True).first.click()
    await page.locator(f'a[href="/c/applications/{app["id"]}"]').first.click()
    await page.route(f"**{url}", fail_patches)
    await page.get_by_label("Сумма").fill("880000")
    await expect(page.get_by_text("Изменения не сохранены")).to_be_visible()
    await page.evaluate("history.back()")
    await expect(page.get_by_text("Переход отменён", exact=False)).to_be_visible()
    await expect(page).to_have_url(f"{base}/c/applications/{app['id']}")
    await expect(page.get_by_label("Сумма")).to_have_value(re.compile(r"^880\s000$"))
    await page.unroute(f"**{url}", fail_patches)
    await page.evaluate("history.back()")
    # пока переход ждёт сохранения, адрес на мгновение уже /c — ждём, когда черновик действительно закроется
    await expect(page.get_by_role("heading", name="Параметры кредита")).to_have_count(0)
    await expect(page).to_have_url(f"{base}/c")
    assert c.req("GET", url)["amount"] == 880_000
    return "кнопка «Назад» не теряет правку при ошибке сохранения"


async def check_logout_during_failure(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    await open_draft(page, base, app["id"])
    await page.route(f"**{url}", fail_patches)
    await page.get_by_label("Сумма").fill("890000")
    await expect(page.get_by_text("Изменения не сохранены")).to_be_visible()
    await page.get_by_label("Выйти").click()
    await expect(page.get_by_text("Выход отменён", exact=False)).to_be_visible()
    await expect(page).to_have_url(f"{base}/c/applications/{app['id']}")
    await expect(page.get_by_label("Сумма")).to_have_value(re.compile(r"^890\s000$"))
    await page.unroute(f"**{url}", fail_patches)
    await page.get_by_label("Выйти").click()
    await expect(page).to_have_url(f"{base}/login")
    assert c.req("GET", url)["amount"] == 890_000
    return "ошибка сохранения отменяет выход; повторный выход сохраняет 890 000 ₽"


async def check_logout_waits_for_inflight_save(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(route: Route) -> None:
        if route.request.method == "PATCH":
            started.set()
            await release.wait()
        await route.continue_()

    await open_draft(page, base, app["id"])
    await page.route(f"**{url}", slow)
    try:
        await page.get_by_label("Сумма").fill("760000")
        await asyncio.wait_for(started.wait(), timeout=10)
        await page.get_by_label("Выйти").click()
        await expect(page.get_by_text("Выход отменён", exact=False)).to_be_visible(timeout=6000)
        await expect(page).to_have_url(f"{base}/c/applications/{app['id']}")
        await expect(page.get_by_label("Сумма")).to_have_value(re.compile(r"^760\s000$"))
    finally:
        release.set()
    saved = await asyncio.to_thread(wait_for, c, url, lambda a: a["amount"] == 760_000)
    assert saved["amount"] == 760_000
    await page.get_by_label("Выйти").click()
    await expect(page).to_have_url(f"{base}/login")
    return "таймаут не закрыл форму во время PATCH; после ответа сервера выход разрешён"


async def check_reload_warns_about_changes(page: Page, base: str) -> str:
    c, app = new_draft(base)
    url = f"/api/v1/applications/{app['id']}"
    await open_draft(page, base, app["id"])
    await page.route(f"**{url}", fail_patches)
    await page.get_by_label("Сумма").fill("810000")
    await expect(page.get_by_text("Изменения не сохранены")).to_be_visible()
    async with page.expect_event("dialog") as event:
        await page.evaluate("setTimeout(() => location.reload(), 0)")
    dialog = await event.value
    assert dialog.type == "beforeunload"
    await dialog.dismiss()
    await expect(page.get_by_label("Сумма")).to_have_value(re.compile(r"^810\s000$"))
    assert c.req("GET", url)["amount"] == 300_000
    return "браузер предупредил перед перезагрузкой; отмена сохранила правку в форме"


LAYOUT_WIDTHS = (320, 390, 768, 1024, 1280, 1920)

# Что вылезает за край окна. Широкие таблицы прокручиваются внутри своей рамки — это не считается,
# кроме очереди заявок: на планшете и телефоне она карточками и в сторону прокручиваться не должна.
OVERFLOW_JS = """() => {
  const vw = document.documentElement.clientWidth;
  const queue = document.querySelector(".queue-table")?.closest(".table-wrap");
  const queueScrolls = vw <= 1040 && queue && queue.scrollWidth > queue.clientWidth + 1;
  const inScroller = (e) => {
    for (let p = e.parentElement; p && p !== document.body; p = p.parentElement)
      if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true;
    return false;
  };
  const out = [];
  for (const e of document.querySelectorAll("body *")) {
    const b = e.getBoundingClientRect();
    if (b.width && b.height && (b.right > vw + 1 || b.left < -1) && !inScroller(e))
      out.push(e.tagName.toLowerCase() + "." + String(e.className).trim().split(/\\s+/).join("."));
  }
  if (queueScrolls) out.unshift("карточки очереди шире экрана");
  return { extra: document.documentElement.scrollWidth - vw, out: out.slice(0, 3) };
}"""


async def check_layout_widths(page: Page, base: str) -> str:
    client, app = new_draft(base)
    # отправленная заявка — чтобы в реестре банка была хотя бы одна карточка
    sent = prepare(client, "EXPRESS", "OTHER", 300_000, 6, purpose_details="Проверка вёрстки")
    client.req("POST", f"/api/v1/applications/{sent['id']}/submit")
    tokens = {"smirnov": client.http.headers["Authorization"].split()[1]}
    for login in ("analyst", "risk"):
        tokens[login] = Client(base, login).http.headers["Authorization"].split()[1]

    def heading(name: str):
        return lambda p: p.get_by_role("heading", name=name, exact=True)

    async def all_applications(p: Page) -> None:
        await p.get_by_role("tab", name="Все").click()
        await p.locator(".queue-table").first.wait_for()

    async def pick_purpose(p: Page) -> None:
        await p.get_by_role("button", name="Закупка товара, сырья, материалов").click()
        await p.get_by_label("Сумма").fill("3000000")
        await heading("Подходящие варианты")(p).wait_for()

    # экран: кто входит, адрес, чего дождаться, что сделать на странице
    screens = [
        (None, "/login", lambda p: p.get_by_text("Войти в один клик"), None),
        ("smirnov", "/c", heading("Кредиты"), None),
        ("smirnov", "/c/match", heading("На что нужны деньги?"), pick_purpose),
        ("smirnov", f"/c/applications/{app['id']}", heading("Параметры кредита"), None),
        ("analyst", "/b", lambda p: p.get_by_role("tab", name="Все"), all_applications),
        ("analyst", "/b/dashboard", heading("Воронка"), None),
        ("risk", "/b/settings", lambda p: p.locator("table tbody tr"), None),
    ]
    await page.emulate_media(reduced_motion="reduce")
    problems: list[str] = []
    for who, path, ready, action in screens:
        await page.set_viewport_size({"width": 1280, "height": 900})
        await page.goto(f"{base}/login")
        await page.evaluate(
            "t => t ? localStorage.setItem('scc.token', t) : localStorage.removeItem('scc.token')",
            tokens.get(who or ""),
        )
        await page.goto(base + path)
        await ready(page).first.wait_for()
        if action:
            await action(page)
        for width in LAYOUT_WIDTHS:
            await page.set_viewport_size({"width": width, "height": 800})
            await page.wait_for_timeout(200)
            info = await page.evaluate(OVERFLOW_JS)
            if info["extra"] > 0 or info["out"]:
                problems.append(f"{path} при ширине {width}: лишние {info['extra']} px, {info['out']}")
    assert not problems, "; ".join(problems[:4])
    return f"{len(screens)} экранов при ширине {', '.join(map(str, LAYOUT_WIDTHS))} px — ничего не уходит за край окна"


CHECKS = [
    ("Ошибка автосохранения не отправляет старую сумму", check_failed_autosave),
    ("Отправка ждёт медленного автосохранения", check_slow_autosave),
    ("Правка сохраняется при уходе из черновика", check_leave_right_after_edit),
    ("Правка сохраняется при выходе из кабинета", check_logout_right_after_edit),
    ("Сбой сохранения отменяет переход", check_navigation_during_failure),
    ("Сбой сохранения отменяет «Назад»", check_back_during_failure),
    ("Сбой сохранения отменяет выход", check_logout_during_failure),
    ("Выход ждёт уже отправленный PATCH", check_logout_waits_for_inflight_save),
    ("Перезагрузка предупреждает о правках", check_reload_warns_about_changes),
    ("Вёрстка от телефона до широкого монитора", check_layout_widths),
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
