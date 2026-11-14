"""Гарантирует, что трафик к Ozon/Wildberries идёт МИМО VPN-туннеля Happ.

Принцип: добавляем host-маршруты (/32) до IP маркетплейсов через физический
шлюз. Host-маршрут всегда выигрывает у маршрута туннеля (0.0.0.0/0 и 0/1),
поэтому парсер получает российский IP даже при включённом глобальном VPN.
Telegram-запросы бота это не затрагивает (они идут через локальный SOCKS5).

Требует прав администратора (route add).

Запуск:  python scripts/ensure_direct_routes.py
"""
import socket
import subprocess
import sys

MARKETPLACE_HOSTS = [
    "ozon.ru",
    "www.ozon.ru",
    "api.ozon.ru",
    "ozonusercontent.com",
    "wildberries.ru",
    "www.wildberries.ru",
    "card.wb.ru",
    "static-basket-01.wbbasket.ru",
]


def get_physical_gateway() -> tuple[str, int]:
    """Ищет шлюз физического интерфейса (не VPN-туннель, не виртуалки)."""
    ps = (
        "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
        "Select-Object ifIndex, InterfaceAlias, NextHop, RouteMetric, InterfaceMetric | "
        "ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    import json
    routes = json.loads(out)
    if isinstance(routes, dict):  # один маршрут
        routes = [routes]

    best = None
    for r in routes:
        alias = (r.get("InterfaceAlias") or "").lower()
        # пропускаем туннель Happ и виртуальные адаптеры
        if any(skip in alias for skip in ("tun", "radmin", "virtualbox", "vethernet", "hyper-v", "wsl", "loopback")):
            continue
        gw = str(r.get("NextHop") or "")
        if not gw or gw == "0.0.0.0":
            continue
        metric = r.get("RouteMetric", 999) + r.get("InterfaceMetric", 999)
        if best is None or metric < best[0]:
            best = (metric, gw, int(r["ifIndex"]))

    if best is None:
        raise RuntimeError("Не найден физический шлюз (VPN выключен или нет сети?)")
    return best[1], best[2]


def resolve_ips(hosts: list[str]) -> set[str]:
    ips: set[str] = set()
    for host in hosts:
        try:
            infos = socket.getaddrinfo(host, 443, socket.AF_INET)
            for info in infos:
                ips.add(info[4][0])
        except OSError as exc:
            print(f"  ! не удалось резолвить {host}: {exc}")
    return ips


def get_current_direct_routes() -> dict[str, tuple[str, int]]:
    """Существующие host-маршруты до маркетплейсов (ip -> (gateway, iface))."""
    out = subprocess.run(
        ["route", "print"], capture_output=True, text=True, encoding="cp866", errors="replace"
    ).stdout
    routes: dict[str, tuple[str, int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1] == "255.255.255.255" and parts[2] in MARKETPLACE_IPS:
            routes[parts[2]] = (parts[3], int(parts[4]))
    return routes


MARKETPLACE_IPS: set[str] = set()


def main() -> None:
    global MARKETPLACE_IPS

    gateway, iface = get_physical_gateway()
    print(f"Физический шлюз: {gateway} (ifIndex {iface})")

    MARKETPLACE_IPS = resolve_ips(MARKETPLACE_HOSTS)
    print(f"IP маркетплейсов: {sorted(MARKETPLACE_IPS)}")

    current = get_current_direct_routes()

    for ip in sorted(MARKETPLACE_IPS):
        if ip in current:
            gw, if_idx = current[ip]
            if gw == gateway:
                print(f"  = маршрут уже есть: {ip} -> {gw}")
                continue
            # шлюз сменился (другая сеть) — перезадаём
            subprocess.run(["route", "delete", ip], capture_output=True)
        result = subprocess.run(
            ["route", "-p", "add", ip, "mask", "255.255.255.255", gateway, "metric", "1", "if", str(iface)],
            capture_output=True, text=True, encoding="cp866", errors="replace",
        )
        if result.returncode == 0:
            print(f"  + добавлен маршрут: {ip} -> {gateway}")
        else:
            print(f"  ! не удалось добавить маршрут {ip}: {result.stdout.strip()} {result.stderr.strip()}")

    # убираем устаревшие маршруты (IP, которых больше нет в DNS)
    for ip in current:
        if ip not in MARKETPLACE_IPS:
            subprocess.run(["route", "delete", ip], capture_output=True)
            print(f"  - удалён устаревший маршрут: {ip}")

    print("Готово.")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Скрипт только для Windows")
    main()
