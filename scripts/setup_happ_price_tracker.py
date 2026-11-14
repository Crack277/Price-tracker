"""Настраивает Happ: добавляет сплит-профиль PriceTracker в routing.json.

Подробности см. docs/happ-vpn-setup.md в корне проекта.
"""
import json
import shutil
from pathlib import Path

ROUTING = Path(r"C:\Users\proec\AppData\Local\Happ\routing.json")

with open(ROUTING, encoding="utf-8") as f:
    data = json.load(f)

names = [r["name"] for r in data["routings"]]
print("existing profiles:", names)

if "PriceTracker" in names:
    print("PriceTracker already exists — nothing to add")
    raise SystemExit(0)

rosx = next(r for r in data["routings"] if r["name"] == "ROS-X")

# Копия профиля ROS-X, но в сплит-режиме (не глобальный прокси):
# российские сайты (в т.ч. ozon.ru, wildberries.ru из geosite:category-ru)
# идут напрямую, остальное — через VPN.
tracker = json.loads(json.dumps(rosx))  # deep copy
tracker["name"] = "PriceTracker"
tracker["globalProxy"] = False

# Своя папка для гео-файлов (копируем готовые, чтобы не качать заново)
src_geo = Path(rosx["locationAssetPath"])
dst_geo = Path(r"C:\Users\proec\AppData\Local\Happ\routing\PriceTracker")
dst_geo.mkdir(parents=True, exist_ok=True)
for fname in ("geoip.dat", "geosite.dat"):
    src = src_geo / fname
    if src.exists() and not (dst_geo / fname).exists():
        shutil.copy2(src, dst_geo / fname)
        print("copied", fname)
tracker["locationAssetPath"] = str(dst_geo)

# Вставляем новый профиль ПЕРВЫМ (в UI он будет сверху)
data["routings"].insert(0, tracker)
data["routingNames"] = ["PriceTracker"] + [n for n in data["routingNames"] if n != "PriceTracker"]
data["activeRoutingName"] = "PriceTracker"

with open(ROUTING, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

print("routing.json updated:")
print("  activeRoutingName =", data["activeRoutingName"])
print("  routingNames =", data["routingNames"])
print("  PriceTracker.globalProxy =", tracker["globalProxy"])
