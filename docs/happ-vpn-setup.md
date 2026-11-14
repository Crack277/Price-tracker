# Настройка Happ (VPN) для price-tracker

## Зачем это нужно

Бот работает в двух режимах трафика, которые конфликтуют друг с другом:

| Трафик | Требуемый маршрут | Почему |
|---|---|---|
| Telegram Bot API | через VPN (зарубежный IP) | Telegram заблокирован в РФ |
| Парсинг Ozon / Wildberries | **напрямую (российский IP)** | Маркетплейсы блокируют зарубежные/VPN IP — отдают заглушки «Похоже, нет соединения», «Доступ ограничен» |

Если VPN работает в **глобальном режиме**, весь трафик машины идёт через зарубежный
выход (Cloudflare и т.п.), и парсинг ломается. Решение — сплит-маршрутизация.

## Что настроено

### 1. Профиль маршрутизации `PriceTracker` (основной механизм)

В `%LOCALAPPDATA%\Happ\routing.json` добавлен профиль **PriceTracker** — копия профиля
ROS-X, но с `globalProxy: false` (сплит-режим):

- `directSites` включает `geosite:category-ru` — в гео-файле Happ есть
  `ozon.ru`, `ozonusercontent.com`, `wildberries.ru`, `wb.ru` → они идут **напрямую**;
- `proxySites` включает `geosite:telegram` и т.п. → идут **через VPN**.

Профиль ROS-X (глобальный) остался нетронутым — можно переключиться обратно в любое
время в настройках Happ.

Гео-файлы скопированы в `%LOCALAPPDATA%\Happ\routing\PriceTracker\`.

### 2. Per-App Proxy (страховка)

В реестре (`HKCU\Software\Happ\...\Preferences\TunnelSettings\PerAppProxy`)
включено исключение из туннеля по процессам:

- `chrome.exe` — браузер, которым парсер открывает страницы Ozon/WB;
- `python.exe` — бот и API-запросы Wildberries (card.wb.ru).

Благодаря этому трафик парсера идёт напрямую даже если в Happ включён глобальный
режим (`ROS-X`). Telegram-запросы бота это не задевает: они идут через явный
SOCKS5-прокси из `.env` (`PROXY_URL=socks5://127.0.0.1:10808`) внутри процесса
`xray.exe`, который сам исключён из туннеля.

## Как это включить (если слетело)

1. Запустить `python scripts/setup_happ_price_tracker.py` — пересоздаст профиль
   PriceTracker в `routing.json` и переключит активный профиль на него.
2. Полностью закрыть Happ (включая трей) и открыть заново, подключиться.
3. В настройках Happ раздел туннелирования (Per-App Proxy / «По процессам»):
   убедиться, что включено и в списке есть `python.exe` и `chrome.exe`
   (режим — «напрямую»/direct).

## Как проверить, что работает

```powershell
# IP из "обычного" питона должен быть российский (не Cloudflare):
python -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org?format=json', timeout=10).read())"

# Парсинг WB через API:
python -c "import asyncio; from app.parsers.registry import get_parser_by_url; print(asyncio.run(get_parser_by_url('https://www.wildberries.ru/catalog/222125424/detail.aspx').parse('https://www.wildberries.ru/catalog/222125424/detail.aspx')))"
```

Если IP зарубежный — Happ подключён в глобальном режиме: выбери профиль
**PriceTracker** в Happ и переподключись.

## Откат (вернуть как было)

1. В Happ выбрать профиль **ROS-X** (глобальный режим).
2. В настройках Happ выключить Per-App Proxy (исключения процессов).
3. Или восстановить резервную копию: `%TEMP%\opencode\routing.json.bak`
   и экспорт реестра `%TEMP%\opencode\happ_reg_backup2.txt`.

## Важно

- Если менял `routing.json` или реестр — **перезапускай Happ полностью**:
  настройки читаются при старте приложения.
- Код парсера дополнительно защищён: браузер парсера запускается с
  `--no-proxy-server` (игнорирует системный прокси Windows) и никогда не
  использует `PROXY_URL` (прокси Telegram). См. `app/parsers/browser.py`.
