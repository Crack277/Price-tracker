# app/parsers/browser.py
"""Общая обвязка для парсинга маркетплейсов через undetected_chromedriver.

Ключевые решения:
- один переиспользуемый headless-браузер (без окон, быстрый повторный парсинг);
- stealth-инъекция через CDP: маскирует признаки headless (UA, userAgentData,
  plugins, WebGL) — иначе анти-бот Ozon/WB распознаёт автоматизацию мгновенно;
- если headless всё же заблокировали — повтор в «невидимом» окне, вынесенном
  за границы экрана;
- трафик всегда прямой (--no-proxy-server): PROXY_URL — это прокси для
  Telegram, с него маркетплейсы отдают страницы-заглушки.
"""
import asyncio
import json
import logging
import re
import subprocess
import threading
import time
import winreg
from decimal import Decimal, InvalidOperation
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.config import get_settings

logger = logging.getLogger(__name__)

PROFILES_DIR = Path("browser_profiles")
PROFILES_DIR.mkdir(exist_ok=True)
SHARED_PROFILE = "shared"

# Метка для поиска наших процессов Chrome среди всех запущенных
_OUR_BROWSER_MARKER = "browser_profiles"


def _kill_orphan_browsers() -> None:
    """Убивает Chrome, осиротевшие после падения прошлых запусков.

    Если такой Chrome держит наш user-data-dir, новый запуск передаёт
    управление ему и мгновенно выходит — chromedriver получает
    «chrome not reachable». Поэтому чистим перед каждым созданием.
    """
    script = (
        "Get-CimInstance Win32_Process -Filter "
        "\"Name='chrome.exe' or Name='chromedriver.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{_OUR_BROWSER_MARKER}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 — подчистка не должна ронять парсинг
        logger.warning("Не удалось подчистить зависшие браузеры: %s", exc)

IDLE_SHUTDOWN_SECONDS = 600  # браузер гасится после 10 минут простоя

# Тексты-маркеры заглушек, которыми маркетплейсы блокируют ботов
BLOCK_MARKERS = (
    "похоже, нет соединения",
    "доступ ограничен",
    "выключите vpn",
    "подтвердите, что запросы отправляли вы",
    "проблема с ip",
    "antibot challenge",
    "доступ восстановите",
    # WB: временная проверка «Подозрительная активность», проходит сама через ~1 мин
    "подозрительная активность",
    "что-то не так",
)

# Цена обязана стоять рядом со знаком валюты — иначе ловим «1 шт» как «1 ₽»
_PRICE_RE = re.compile(r"(\d[\d\s\u00A0]*?)\s*(?:₽|руб)", re.IGNORECASE)


class ParseBlockedError(RuntimeError):
    """Маркетплейс отдал страницу-заглушку вместо карточки товара."""

    def __init__(self, marker: str) -> None:
        super().__init__(f"Маркетплейс отдал заглушку вместо товара (маркер: {marker!r})")
        self.marker = marker


def looks_like_block_page(html: str) -> str | None:
    """Возвращает сработавший маркер блокировки или None."""
    low = html.lower()
    for marker in BLOCK_MARKERS:
        if marker in low:
            return marker
    return None


def parse_price_text(text: str) -> Decimal | None:
    """Достаёт цену из текста вида '1 999 ₽' / '1 200 руб'."""
    match = _PRICE_RE.search(text)
    if match is None:
        return None
    raw = re.sub(r"[^\d]", "", match.group(1))
    if not raw:
        return None
    try:
        price = Decimal(raw)
    except InvalidOperation:
        return None
    if price <= 0 or price > Decimal(10_000_000):
        return None
    return price


def extract_jsonld_price(driver) -> Decimal | None:
    """Ищет цену в JSON-LD разметке (schema.org Product/Offer)."""
    try:
        scripts = driver.find_elements(
            By.CSS_SELECTOR, 'script[type="application/ld+json"]'
        )
    except WebDriverException:
        return None
    for script in reversed(scripts):
        raw = script.get_attribute("innerHTML") or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for offer in _iter_offers(data):
            price = offer.get("price") or offer.get("lowPrice")
            if price is None:
                continue
            try:
                value = Decimal(str(price).replace(" ", "").replace(",", "."))
            except InvalidOperation:
                continue
            if 0 < value <= Decimal(10_000_000):
                return value
    return None


def _iter_offers(data):
    """Рекурсивно собирает объекты с полем price/lowPrice из JSON-LD."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "price" in node or "lowPrice" in node:
                yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


# ---------------------------------------------------------------------------
# Chrome / UA
# ---------------------------------------------------------------------------

def _chrome_full_version() -> str | None:
    for hive, path in (
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
        # Edge — fallback-браузер парсера, если Chrome не установлен
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Edge\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Edge\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Edge\BLBeacon"),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                return winreg.QueryValueEx(key, "version")[0]
        except OSError:
            continue
    return None


def _chrome_major_version() -> str | None:
    full = _chrome_full_version()
    if full:
        return full.split(".")[0]
    logger.warning("Не удалось прочитать версию Chrome из реестра — пусть uc сам определит")
    return None


def _real_ua(major: str) -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


# Stealth-скрипт: маскирует признаки headless до загрузки страницы.
# UA и client-hints задаются нативно через CDP (см. _apply_stealth),
# здесь — только то, что CDP не покрывает.
_STEALTH_JS_TEMPLATE = r"""
(function() {
  var MAJOR = "{MAJOR}";

  // --- Инфраструктура: подмена Function.prototype.toString -----------------
  // Анти-бот проверяет подменённые функции вызовом toString(): у настоящих
  // нативных функций он возвращает "[native code]", у наших патчей — исходник.
  var nativeToString = Function.prototype.toString;
  var nativeSources = new WeakMap();
  function markNative(fn, name) {
    nativeSources.set(fn, "function " + name + "() { [native code] }");
    return fn;
  }
  var patchedToString = markNative(function toString() {
    var s = nativeSources.get(this);
    return s !== undefined ? s : nativeToString.call(this);
  }, "toString");
  try {
    Object.defineProperty(Function.prototype, "toString", {
      value: patchedToString, writable: true, configurable: true,
    });
  } catch (e) {}

  function defGet(obj, prop, getter, name) {
    try {
      Object.defineProperty(obj, prop, {
        get: markNative(getter, "get " + (name || prop)),
        configurable: true,
      });
    } catch (e) {}
  }

  // --- navigator.webdriver --------------------------------------------------
  defGet(navigator, "webdriver", function() { return undefined; });

  // --- screen: в headless Chrome рапортует 800x600 — мгновенный детект ------
  (function() {
    var dims = {
      width: 1920, height: 1080, availWidth: 1920, availHeight: 1040,
      availLeft: 0, availTop: 0, colorDepth: 24, pixelDepth: 24,
    };
    for (var key in dims) {
      (function(k, v) {
        defGet(screen, k, function() { return v; });
      })(key, dims[key]);
    }
  })();

  // --- deviceMemory: в headless отсутствует ---------------------------------
  defGet(navigator, "deviceMemory", function() { return 8; });

  // --- plugins / mimeTypes с настоящими прототипами -------------------------
  // В headless список пуст; подделка должна проходить проверку
  // Object.prototype.toString.call(...) === "[object PluginArray]".
  (function() {
    if (typeof Plugin === "undefined" || typeof PluginArray === "undefined") return;
    var names = [
      ["PDF Viewer", "Portable Document Format", "internal-pdf-viewer"],
      ["Chrome PDF Viewer", "Portable Document Format", "internal-pdf-viewer"],
      ["Chromium PDF Viewer", "Portable Document Format", "internal-pdf-viewer"],
      ["Microsoft Edge PDF Viewer", "Portable Document Format", "internal-pdf-viewer"],
      ["WebKit built-in PDF", "Portable Document Format", "internal-pdf-viewer"],
    ];
    var plugins = names.map(function(n) {
      var p = Object.create(Plugin.prototype);
      Object.defineProperties(p, {
        name: { value: n[0], enumerable: true },
        description: { value: n[1], enumerable: true },
        filename: { value: n[2], enumerable: true },
        length: { value: 1, enumerable: true },
      });
      return p;
    });
    var pluginArray = Object.create(PluginArray.prototype);
    Object.defineProperty(pluginArray, "length", {
      get: markNative(function() { return plugins.length; }, "get length"),
      configurable: true, enumerable: true,
    });
    plugins.forEach(function(p, i) {
      Object.defineProperty(pluginArray, String(i), {
        get: markNative(function() { return p; }, "get " + i),
        configurable: true, enumerable: true,
      });
      Object.defineProperty(pluginArray, p.name, {
        get: markNative(function() { return p; }, "get " + p.name),
        configurable: true, enumerable: false,
      });
    });
    pluginArray.item = markNative(function item(i) {
      return i >= 0 && i < plugins.length ? plugins[i] : null;
    }, "item");
    pluginArray.namedItem = markNative(function namedItem(name) {
      for (var i = 0; i < plugins.length; i++) if (plugins[i].name === name) return plugins[i];
      return null;
    }, "namedItem");
    defGet(navigator, "plugins", function() { return pluginArray; });

    if (typeof MimeType === "undefined" || typeof MimeTypeArray === "undefined") return;
    var mime = Object.create(MimeType.prototype);
    Object.defineProperties(mime, {
      type: { value: "application/pdf", enumerable: true },
      suffixes: { value: "pdf", enumerable: true },
      description: { value: "", enumerable: true },
      enabledPlugin: { get: markNative(function() { return plugins[0]; }, "get enabledPlugin"), configurable: true },
    });
    var mimeArray = Object.create(MimeTypeArray.prototype);
    Object.defineProperty(mimeArray, "length", {
      get: markNative(function() { return 1; }, "get length"),
      configurable: true, enumerable: true,
    });
    Object.defineProperty(mimeArray, "0", {
      get: markNative(function() { return mime; }, "get 0"),
      configurable: true, enumerable: true,
    });
    Object.defineProperty(mimeArray, "application/pdf", {
      get: markNative(function() { return mime; }, "get application/pdf"),
      configurable: true, enumerable: false,
    });
    mimeArray.item = markNative(function item(i) { return i === 0 ? mime : null; }, "item");
    mimeArray.namedItem = markNative(function namedItem(name) {
      return name === "application/pdf" ? mime : null;
    }, "namedItem");
    defGet(navigator, "mimeTypes", function() { return mimeArray; });
  })();

  // --- userAgentData: создаём, если в headless отсутствует ------------------
  (function() {
    if (typeof navigator.userAgentData !== "undefined") return;
    if (typeof NavigatorUAData !== "function") return;
    var uad = Object.create(NavigatorUAData.prototype);
    var brands = [
      { brand: "Chromium", version: MAJOR },
      { brand: "Google Chrome", version: MAJOR },
      { brand: "Not?A_Brand", version: "99" },
    ];
    Object.defineProperties(uad, {
      brands: { get: markNative(function() { return brands; }, "get brands"), configurable: true, enumerable: true },
      mobile: { get: markNative(function() { return false; }, "get mobile"), configurable: true, enumerable: true },
      platform: { get: markNative(function() { return "Windows"; }, "get platform"), configurable: true, enumerable: true },
    });
    uad.getHighEntropyValues = markNative(function(hints) {
      var values = {};
      var known = {
        brands: brands, mobile: false, platform: "Windows",
        platformVersion: "10.0.0", architecture: "x86", bitness: "64",
        model: "", uaFullVersion: MAJOR + ".0.0.0", wow64: false,
        formFactors: ["Desktop"],
        fullVersionList: [
          { brand: "Chromium", version: MAJOR + ".0.0.0" },
          { brand: "Google Chrome", version: MAJOR + ".0.0.0" },
          { brand: "Not?A_Brand", version: "99.0.0.0" },
        ],
      };
      for (var i = 0; i < hints.length; i++) {
        if (Object.prototype.hasOwnProperty.call(known, hints[i])) values[hints[i]] = known[hints[i]];
      }
      return Promise.resolve(values);
    }, "getHighEntropyValues");
    uad.toJSON = markNative(function toJSON() {
      return { brands: brands, mobile: false, platform: "Windows" };
    }, "toJSON");
    Object.defineProperty(navigator, "userAgentData", {
      get: markNative(function() { return uad; }, "get userAgentData"),
      configurable: true,
    });
  })();

  // --- permissions: notifications должны быть "prompt", а не "denied" -------
  // Патчим реальный PermissionStatus (сохраняем instanceof и прототип).
  (function() {
    if (typeof Permissions === "undefined" || !navigator.permissions) return;
    var origQuery = navigator.permissions.query;
    var patchedQuery = markNative(function query(params) {
      var promise = origQuery.apply(this, arguments);
      if (params && params.name === "notifications") {
        return promise.then(function(status) {
          try {
            Object.defineProperty(status, "state", {
              get: markNative(function() { return "prompt"; }, "get state"),
              configurable: true,
            });
          } catch (e) {}
          return status;
        });
      }
      return promise;
    }, "query");
    try {
      Object.defineProperty(Permissions.prototype, "query", {
        value: patchedQuery, writable: true, configurable: true,
      });
    } catch (e) {}
  })();

  // --- speechSynthesis: в headless список голосов пуст -----------------------
  (function() {
    if (typeof SpeechSynthesis === "undefined" || typeof SpeechSynthesisVoice === "undefined") return;
    var list = [
      ["Microsoft David - English (United States)", "en-US", true],
      ["Microsoft Zira - English (United States)", "en-US", true],
      ["Microsoft Mark - English (United States)", "en-US", true],
      ["Microsoft Aria Online (Natural) - English (United States)", "en-US", false],
      ["Microsoft Jenny Online (Natural) - English (United States)", "en-US", false],
      ["Microsoft Guy Online (Natural) - English (United States)", "en-US", false],
      ["Microsoft Irina - Russian (Russia)", "ru-RU", true],
      ["Microsoft Pavel - Russian (Russia)", "ru-RU", true],
      ["Microsoft Svetlana Online (Natural) - Russian (Russia)", "ru-RU", false],
      ["Microsoft Dmitry Online (Natural) - Russian (Russia)", "ru-RU", false],
      ["Microsoft George - English (United Kingdom)", "en-GB", true],
      ["Microsoft Hazel - English (United Kingdom)", "en-GB", true],
      ["Microsoft Susan - English (United Kingdom)", "en-GB", true],
      ["Microsoft Ryan Online (Natural) - English (United Kingdom)", "en-GB", false],
      ["Microsoft Libby Online (Natural) - English (United Kingdom)", "en-GB", false],
      ["Microsoft Hedda - German (Germany)", "de-DE", true],
      ["Microsoft Katja - German (Germany)", "de-DE", true],
      ["Microsoft Conrad - German (Germany)", "de-DE", true],
      ["Microsoft Hortense - French (France)", "fr-FR", true],
      ["Microsoft Paul - French (France)", "fr-FR", true],
      ["Microsoft Eloise Online (Natural) - French (France)", "fr-FR", false],
      ["Microsoft Helena - Spanish (Spain)", "es-ES", true],
      ["Microsoft Pablo - Spanish (Spain)", "es-ES", true],
      ["Microsoft Cosimo - Italian (Italy)", "it-IT", true],
      ["Microsoft Elsa - Italian (Italy)", "it-IT", true],
      ["Microsoft Fernanda - Portuguese (Brazil)", "pt-BR", true],
      ["Microsoft Daniel - Portuguese (Brazil)", "pt-BR", true],
      ["Microsoft Haruka - Japanese (Japan)", "ja-JP", true],
      ["Microsoft Nanami Online (Natural) - Japanese (Japan)", "ja-JP", false],
      ["Microsoft Huihui - Chinese (Simplified, China)", "zh-CN", true],
      ["Microsoft Kangkang - Chinese (Simplified, China)", "zh-CN", true],
      ["Google русский", "ru-RU", false],
      ["Google US English", "en-US", false],
    ];
    var voices = list.map(function(v, i) {
      var voice = Object.create(SpeechSynthesisVoice.prototype);
      Object.defineProperties(voice, {
        voiceURI: { value: v[0], enumerable: true },
        name: { value: v[0], enumerable: true },
        lang: { value: v[1], enumerable: true },
        localService: { value: v[2], enumerable: true },
        default: { value: i === 0, enumerable: true },
      });
      return voice;
    });
    try {
      Object.defineProperty(SpeechSynthesis.prototype, "getVoices", {
        value: markNative(function getVoices() { return voices; }, "getVoices"),
        writable: true, configurable: true,
      });
    } catch (e) {}
  })();

  // --- window.chrome ---------------------------------------------------------
  try {
    if (!window.chrome) {
      window.chrome = {
        runtime: {}, app: { isInstalled: false },
        csi: markNative(function csi() {}, "csi"),
        loadTimes: markNative(function loadTimes() {}, "loadTimes"),
      };
    }
  } catch (e) {}

  // --- WebGL: не показываем программный рендер -------------------------------
  (function() {
    var patch = function(proto) {
      if (!proto) return;
      var orig = proto.getParameter;
      if (!orig) return;
      proto.getParameter = markNative(function getParameter(p) {
        var v = orig.apply(this, arguments);
        if (typeof v === "string" && /swiftshader|llvmpipe|software/i.test(v)) {
          return p === 37445 ? "Intel Inc." : "Intel Iris OpenGL Engine";
        }
        return v;
      }, "getParameter");
    };
    patch(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
    patch(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  })();
})();
"""


class _SharedBrowser:
    """Один headless-браузер на весь процесс: создаётся лениво, гасится в простое."""

    def __init__(self) -> None:
        self._driver: uc.Chrome | None = None
        self._idle_timer: threading.Timer | None = None
        # блокировка на время всего парсинга — драйвер не потокобезопасен
        self.lock = threading.Lock()

    def acquire(self, headless: bool = True) -> tuple[uc.Chrome, bool]:
        """Возвращает (драйвер, временный_ли). Вызывать под lock."""
        self._cancel_idle_shutdown()
        if self._driver is not None and not self._is_alive(self._driver):
            logger.warning("Общий браузер умер — пересоздаём")
            self._driver = None
        if self._driver is None and headless:
            self._driver = self._create(headless=True)
            return self._driver, False
        if headless:
            return self._driver, False
        # видимый (off-screen) режим — всегда отдельный временный браузер
        return self._create(headless=False), True

    def release(self, driver: uc.Chrome, temporary: bool) -> None:
        if temporary:
            try:
                driver.quit()
            except Exception:
                pass
            return
        self._schedule_idle_shutdown()

    def drop(self) -> None:
        """Принудительно погасить общий браузер (при падении сессии)."""
        driver, self._driver = self._driver, None
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    def _is_alive(self, driver: uc.Chrome) -> bool:
        try:
            _ = driver.window_handles
            return True
        except Exception:
            try:
                driver.quit()
            except Exception:
                pass
            return False

    def _create(self, headless: bool) -> uc.Chrome:
        settings = get_settings()
        user_data_dir = PROFILES_DIR / SHARED_PROFILE
        user_data_dir.mkdir(exist_ok=True)

        _kill_orphan_browsers()

        browser_exe = _find_browser_exe()
        if browser_exe is None:
            raise RuntimeError(
                "Не найден браузер (chrome.exe/msedge.exe) — установите Chrome "
                "или Edge, headless-парсер работает только через них"
            )

        major = _chrome_major_version()
        options = uc.ChromeOptions()
        options.add_argument(f"--user-data-dir={user_data_dir.absolute()}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=ru-RU")
        # ВАЖНО: не отключаем картинки (--blink-settings=imagesEnabled=false) —
        # анти-бот Ozon проверяет загрузку изображений в челлендже,
        # с отключёнными картинками он сразу выдаёт инцидент.
        options.page_load_strategy = "eager"

        # Отдельный прокси для парсера (если задан), иначе — строго напрямую.
        parser_proxy = settings.parser_proxy_url_or_none
        if parser_proxy:
            options.add_argument(f"--proxy-server={parser_proxy}")
        else:
            options.add_argument("--no-proxy-server")

        if headless:
            options.add_argument("--headless=new")
            # в headless Chrome сам подставляет HeadlessChrome в HTTP-заголовок
            if major:
                options.add_argument(f"user-agent={_real_ua(major)}")
        else:
            # окно за пределами экрана: пользователю ничего не видно
            options.add_argument("--window-position=-32000,-32000")

        if major:
            driver = uc.Chrome(
                options=options,
                headless=headless,
                version_main=int(major),
                browser_executable_path=browser_exe,
            )
        else:
            driver = uc.Chrome(
                options=options, headless=headless, browser_executable_path=browser_exe
            )
        return driver

    def _schedule_idle_shutdown(self) -> None:
        self._cancel_idle_shutdown()
        self._idle_timer = threading.Timer(
            IDLE_SHUTDOWN_SECONDS, self._shutdown_if_idle
        )
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _cancel_idle_shutdown(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _shutdown_if_idle(self) -> None:
        with self.lock:
            self.drop()


_shared_browser = _SharedBrowser()


# ---------------------------------------------------------------------------
# Посев доверия анти-бота настоящим Chrome (без автоматизации)
# ---------------------------------------------------------------------------

def _find_browser_exe() -> str | None:
    """Путь к Chromium-браузеру: сначала Chrome, затем Edge (fallback)."""
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    for path in candidates:
        if Path(path).exists():
            return path
    # Браузер могли установить не в стандартные папки — смотрим реестр
    for name in ("chrome.exe", "msedge.exe"):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}",
            ) as key:
                exe = winreg.QueryValueEx(key, "")[0]
                if exe and Path(exe).exists():
                    return exe
        except OSError:
            continue
    return None


def _find_chrome_exe() -> str | None:
    """Путь к установленному браузеру (тот же бинарник, что и у пользователя)."""
    return _find_browser_exe()


def _profile_has_ozon_trust() -> bool:
    """Есть ли в профиле кука abt_data (доверие анти-бота Ozon)."""
    cookie_db = PROFILES_DIR / SHARED_PROFILE / "Default" / "Network" / "Cookies"
    if not cookie_db.exists():
        return False
    import shutil
    import sqlite3
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmp:
            # Chrome пишет куки через WAL/journal — копируем всё вместе,
            # иначе свежая кука останется в journal и копия её не покажет
            for suffix in ("", "-journal", "-wal", "-shm"):
                src = cookie_db.with_name("Cookies" + suffix)
                if src.exists():
                    shutil.copy2(src, Path(tmp) / ("Cookies" + suffix))
            conn = sqlite3.connect(str(Path(tmp) / "Cookies"))
            try:
                row = conn.execute(
                    "SELECT 1 FROM cookies "
                    "WHERE host_key LIKE '%ozon%' AND name = 'abt_data' LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
        return row is not None
    except Exception:  # noqa: BLE001 — любая ошибка чтения = считаем, что куки нет
        return False


def _close_seed_chrome() -> None:
    """Корректно закрывает посевной Chrome (по нашему профилю в cmdline)."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{_OUR_BROWSER_MARKER}*' }} | "
        "ForEach-Object { "
        "(Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).CloseMainWindow() "
        "| Out-Null }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True, timeout=30, check=False,
        )
    except Exception:
        pass
    # даём Chrome 8 сек на корректный выход (сброс кук на диск)
    for _ in range(16):
        time.sleep(0.5)
        script_check = (
            "(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{_OUR_BROWSER_MARKER}*' }} "
            "| Measure-Object).Count"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script_check],
                capture_output=True, timeout=15, check=False, text=True,
            )
            if result.stdout.strip() in ("0", ""):
                return
        except Exception:
            pass
    _kill_orphan_browsers()


def seed_profile_via_real_chrome(url: str, max_wait: float = 60.0) -> bool:
    """Прогоняет URL через настоящий Chrome (без chromedriver/CDP).

    Анти-бот Ozon пропускает настоящий браузер и выдаёт в профиль куку
    abt_data — дальше headless-браузер парсера ходит с этим доверием.
    Окно маленькое, в углу экрана, закрывается само после получения
    доверия. Нужен редко: кука abt_data живёт долго.
    """
    chrome = _find_chrome_exe()
    if chrome is None:
        logger.warning("Chrome не найден — посев доверия невозможен")
        return False

    logger.info("Посев доверия анти-бота через настоящий Chrome (короткое окно)")
    _shared_browser.drop()  # освобождаем профиль

    user_data_dir = (PROFILES_DIR / SHARED_PROFILE).absolute()
    # ВАЖНО: окно в РЕАЛЬНОЙ позиции экрана (маленькое, в правом нижнем
    # углу). Анти-бот проверяет правдоподобность window.screenX/Y —
    # позиция -32000 мгновенно заваливает челлендж.
    cmd = [
        chrome,
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-position=1300,600",
        "--window-size=560,440",
        url,
    ]
    try:
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось запустить посевной Chrome: %s", exc)
        return False

    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        time.sleep(3)
        if _profile_has_ozon_trust():
            logger.info("Кука abt_data получена — закрываем посевной браузер")
            _close_seed_chrome()
            return True
    logger.warning("Доверие за %.0f сек не получено", max_wait)
    _close_seed_chrome()
    return False


def _apply_stealth(driver: uc.Chrome) -> None:
    """Нативная маскировка headless: CDP-оверрайд UA + JS-патчи."""
    if getattr(driver, "_price_tracker_stealth", False):
        return
    full = _chrome_full_version()
    major = (full or "130").split(".")[0]
    ua = _real_ua(major)

    # UA и client-hints задаются нативно: совпадают и HTTP-заголовки,
    # и navigator.userAgentData, без JS-подмен.
    metadata = {
        "brands": [
            {"brand": "Chromium", "version": major},
            {"brand": "Google Chrome", "version": major},
            {"brand": "Not?A_Brand", "version": "99"},
        ],
        "fullVersionList": [
            {"brand": "Chromium", "version": full or f"{major}.0.0.0"},
            {"brand": "Google Chrome", "version": full or f"{major}.0.0.0"},
            {"brand": "Not?A_Brand", "version": "99.0.0.0"},
        ],
        "fullVersion": full or f"{major}.0.0.0",
        "platform": "Windows",
        "platformVersion": "10.0.0",
        "architecture": "x86",
        "model": "",
        "mobile": False,
        "bitness": "64",
        "wow64": False,
        "formFactors": ["Desktop"],
    }
    try:
        driver.execute_cdp_cmd(
            "Network.setUserAgentOverride",
            {
                "userAgent": ua,
                # без "q=": Chrome сам добавляет веса, а с q он портит
                # navigator.languages (вставляет "ru;q=0.9" как отдельный тег)
                "acceptLanguage": "ru-RU,ru",
                "platform": "Win32",
                "userAgentMetadata": metadata,
            },
        )
    except WebDriverException as exc:
        logger.warning("Не удалось задать UA через CDP: %s", str(exc)[:100])

    # JS-патчи: screen, plugins, permissions, toString и т.п.
    js = _STEALTH_JS_TEMPLATE.replace("{MAJOR}", major)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": js})
    driver._price_tracker_stealth = True


# ---------------------------------------------------------------------------
# Основной API
# ---------------------------------------------------------------------------

async def get_page_data_async(
    url: str,
    price_selector: str,
    title_selector: str,
    wait_time: int = 20,
    headless: bool = True,
) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _get_page_data_sync,
        url,
        price_selector,
        title_selector,
        wait_time,
        headless,
    )


def _get_page_data_sync(
    url: str,
    price_selector: str,
    title_selector: str,
    wait_time: int,
    headless: bool,
) -> dict:
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            with _shared_browser.lock:
                driver, temporary = _shared_browser.acquire(headless=headless)
                try:
                    _apply_stealth(driver)
                    return _extract(
                        driver, url, price_selector, title_selector, wait_time
                    )
                finally:
                    _shared_browser.release(driver, temporary)
        except WebDriverException as exc:
            last_exc = exc
            logger.warning("Сбой сессии браузера (попытка %d/2): %s", attempt, str(exc)[:120])
            _shared_browser.drop()
        except ParseBlockedError:
            raise
        except Exception as exc:
            last_exc = exc
            break
    raise last_exc if last_exc else RuntimeError("Не удалось получить страницу")


def _extract(
    driver: uc.Chrome,
    url: str,
    price_selector: str,
    title_selector: str,
    wait_time: int,
) -> dict:
    driver.get(url)

    try:
        WebDriverWait(driver, wait_time).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, price_selector))
        )
    except TimeoutException:
        pass

    time.sleep(1.5)  # даём JS дорисовать блок с ценой

    html = driver.page_source
    marker = looks_like_block_page(html)
    if marker:
        raise ParseBlockedError(marker)

    price_text = ""
    try:
        price_el = driver.find_element(By.CSS_SELECTOR, price_selector)
        price_text = price_el.text
    except Exception:
        pass

    title_text = ""
    try:
        title_el = driver.find_element(By.CSS_SELECTOR, title_selector)
        title_text = title_el.text.strip()
    except Exception:
        pass

    return {
        "price_text": price_text,
        "title_text": title_text,
        "page_title": driver.title or "",
        "html": html,
        "jsonld_price": extract_jsonld_price(driver),
    }
