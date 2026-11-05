# PriceTracker

Telegram-бот для отслеживания изменения цен на маркетплейсах (Ozon, Wildberries) с уведомлениями о снижении цены.

## Стек

- Python 3.11+
- aiogram 3.x
- PostgreSQL + SQLAlchemy 2.0 (async)
- Redis + APScheduler
- undetected-chromedriver + Selenium — headless-парсинг Ozon и Wildberries (см. «Как работает парсинг»)

## VPN / маршрутизация (важно!)

Бот ходит в Telegram через VPN (он заблокирован в РФ), а парсер должен ходить
напрямую с российского IP — маркетплейсы блокируют зарубежные адреса.

Настройка сплит-маршрутизации Happ (профиль `PriceTracker` + Per-App Proxy
для `python.exe`/`chrome.exe`) и её проверка описаны в
[docs/happ-vpn-setup.md](docs/happ-vpn-setup.md).

**Критично:** `PARSER_PROXY_URL` в `.env` должен оставаться ПУСТЫМ. Если туда
попадёт прокси VPN, парсер пойдёт на маркетплейсы с зарубежного IP и получит
заглушки анти-бота. Запасной способ обхода VPN для трафика парсера —
host-маршруты до CDN маркетплейсов: `scripts/ensure_direct_routes.py`
(запускать от администратора после смены CDN-адресов; актуальность можно
проверить через `Find-NetRoute -RemoteIPAddress <ip>`).

## Как работает парсинг

`app/parsers/browser.py` — общий модуль для обоих маркетплейсов:

1. **Один переиспользуемый headless-браузер** (`_SharedBrowser`): запускается
   лениво, живёт между парсингами и гасится после 10 минут простоя.
   Повторный парсинг занимает ~2-4 сек, первый после простоя ~10-15 сек
   (старт браузера).
2. **Stealth-маскировка headless**: `Network.setUserAgentOverride` (нативные
   User-Agent и client-hints — заголовки совпадают с JS), плюс скрипт через
   `Page.addScriptToEvaluateOnNewDocument`, который патчит `screen`
   (headless рапортует 800x600), `navigator.plugins/mimeTypes` (с настоящими
   прототипами PluginArray/MimeTypeArray), `permissions.query`,
   `speechSynthesis.getVoices`, `deviceMemory` и подделывает
   `Function.prototype.toString`, чтобы патчи выглядели «native code».
   Без этого анти-боты выдают страницы-инциденты.
3. **Посев доверия** (`seed_profile_via_real_chrome`): если анти-бот всё же
   выдал страницу-инцидент, парсер запускает настоящий Chrome (без
   chromedriver/CDP) с общим профилем на тот же URL. Челлендж решается сам,
   куки доверия (`abt_data`) пишутся в общий профиль, дальше headless
   парсинг проходит сам. Окно маленькое, в углу экрана, закрывается само
   после получения доверия. Нужно редко — кука живёт долго.
   Позиция окна должна быть реальной (не off-screen): анти-бот проверяет
   правдоподобность `window.screenX/Y`.
4. **Запасной путь** — off-screen окно (`--window-position=-32000,-32000`),
   работает как обычное окно, но не видно пользователю.
5. **Трафик всегда прямой** (`--no-proxy-server`): PROXY_URL — только для
   Telegram.

Wildberries дополнительно пробует API `card.wb.ru` (быстрый путь, если WAF
его пропускает), но основной путь — браузер: цена берётся из
`[data-testid="product-price-final"]` или `<title>` («... купить за N ₽»).

## Статус

В разработке.
