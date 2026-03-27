#!/usr/bin/env python3
"""
DiskMap Health Scanner
Сканує підключені диски через system_profiler і оновлює статус в DiskMap.

Використання:
    python3 diskmap_health.py

Потрібен Python 3.6+ (є на будь-якому Mac)
"""

import json
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime

# ============================================================
# КОНФІГУРАЦІЯ — заповни свої дані
# ============================================================
WORKER_URL = "https://diskmap-api.ibondarenko.workers.dev"

# ============================================================

def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout, result.returncode

def get_storage_info():
    """Отримує інформацію про всі диски через system_profiler"""
    print("⟳ Сканування дисків...")
    out, code = run(["system_profiler", "SPStorageDataType", "-json"])
    if code != 0:
        print("✗ Помилка system_profiler")
        return []
    try:
        data = json.loads(out)
        return data.get("SPStorageDataType", [])
    except:
        return []

def get_nvme_info():
    """NVMe диски — мають температуру"""
    out, code = run(["system_profiler", "SPNVMeDataType", "-json"])
    if code != 0:
        return []
    try:
        data = json.loads(out)
        return data.get("SPNVMeDataType", [])
    except:
        return []

def get_disk_list():
    """Список фізичних дисків через diskutil"""
    out, code = run(["diskutil", "list", "-plist"])
    if code != 0:
        return {}
    try:
        import plistlib
        data = plistlib.loads(out.encode())
        disks = {}
        for disk in data.get("WholeDisks", []):
            info_out, _ = run(["diskutil", "info", "-plist", disk])
            try:
                info = plistlib.loads(info_out.encode())
                disks[disk] = {
                    "name": info.get("MediaName", disk),
                    "size": info.get("TotalSize", 0),
                    "protocol": info.get("BusProtocol", ""),
                    "smart": info.get("SMARTStatus", "Not Supported"),
                    "removable": info.get("RemovableMedia", False),
                    "model": info.get("MediaType", ""),
                    "volumes": []
                }
            except:
                pass
        return disks
    except:
        return {}

def fetch_index():
    """Завантажує індекс дисків з Worker"""
    try:
        with urllib.request.urlopen(WORKER_URL + "/index") as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"✗ Помилка завантаження індексу: {e}")
        return {}

def patch_index(data):
    """Зберігає оновлений індекс через Worker"""
    try:
        req = urllib.request.Request(
            WORKER_URL + "/index",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="PATCH"
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"✗ Помилка збереження: {e}")
        return None

def smart_status_to_health(smart_str):
    """Конвертує SMART статус macOS в наш формат"""
    s = smart_str.lower()
    if "verified" in s or "passed" in s or "ok" in s:
        return {"status": "ok", "label": "Verified", "icon": "💚"}
    elif "failing" in s or "failed" in s or "fail" in s:
        return {"status": "critical", "label": "Failing", "icon": "❌"}
    elif "caution" in s or "warning" in s:
        return {"status": "warning", "label": "Caution", "icon": "⚠️"}
    else:
        return {"status": "unknown", "label": smart_str or "Not Supported", "icon": "⚪"}

def format_size(bytes_val):
    gb = bytes_val / 1024 / 1024 / 1024
    if gb >= 1000:
        return f"{gb/1000:.1f} TB"
    return f"{gb:.0f} GB"

def main():
    print("=" * 50)
    print("  DiskMap Health Scanner")
    print("=" * 50)
    print()

    # Скануємо диски
    disks = get_disk_list()
    if not disks:
        print("✗ Не вдалось отримати список дисків")
        sys.exit(1)

    # Фільтруємо тільки зовнішні/знімні диски
    external = {k: v for k, v in disks.items() if v.get("removable") or v.get("protocol") in ("USB", "Thunderbolt", "USB 3.1")}

    if not external:
        print("ℹ Зовнішніх дисків не знайдено")
        print()
        print("Всі знайдені диски:")
        for disk, info in disks.items():
            print(f"  {disk}: {info['name']} ({info['protocol']}) — SMART: {info['smart']}")
        print()
        answer = input("Показати всі диски для вибору? [y/n]: ").strip().lower()
        if answer == 'y':
            external = disks
        else:
            sys.exit(0)

    # Показуємо знайдені диски
    print(f"Знайдено {len(external)} зовнішніх дисків:\n")
    disk_list = list(external.items())
    for i, (disk, info) in enumerate(disk_list):
        health = smart_status_to_health(info["smart"])
        size = format_size(info["size"]) if info["size"] else "?"
        print(f"  [{i+1}] {health['icon']} {info['name'] or disk}")
        print(f"       {disk} · {size} · SMART: {health['label']} · {info['protocol']}")
        print()

    # Завантажуємо поточний індекс
    print("⟳ Завантажуємо DiskMap індекс...")
    index = fetch_index()
    diskmap_disks = {k: v for k, v in index.items() if v and v.get("id")}

    if not diskmap_disks:
        print("ℹ В DiskMap поки немає дисків")
    else:
        print(f"В DiskMap знайдено {len(diskmap_disks)} дисків:")
        for i, (did, d) in enumerate(diskmap_disks.items()):
            health = d.get("health", {})
            icon = health.get("icon", "⚪") if health else "⚪"
            print(f"  [{i+1}] {icon} {d['name']}")
        print()

    # Вибираємо який диск оновлювати
    print("-" * 50)
    print("Для кожного фізичного диску вкажи відповідний в DiskMap\n")

    updated = 0
    for disk, info in disk_list:
        health = smart_status_to_health(info["smart"])
        size = format_size(info["size"]) if info["size"] else "?"
        print(f"Фізичний диск: {health['icon']} {info['name'] or disk} ({size})")
        print(f"SMART: {health['label']}")

        if not diskmap_disks:
            print("В DiskMap немає дисків — пропускаємо")
            print()
            continue

        dm_list = list(diskmap_disks.items())
        print("Вибери диск в DiskMap (або 0 щоб пропустити):")
        for i, (did, d) in enumerate(dm_list):
            print(f"  [{i+1}] {d['name']}")

        try:
            choice = input("Вибір: ").strip()
            if not choice or choice == "0":
                print("→ Пропускаємо\n")
                continue
            idx = int(choice) - 1
            if idx < 0 or idx >= len(dm_list):
                print("→ Невірний вибір, пропускаємо\n")
                continue
        except (ValueError, KeyboardInterrupt):
            print("\n→ Пропускаємо\n")
            continue

        disk_id, disk_entry = dm_list[idx]

        # Формуємо health об'єкт
        health_data = {
            "status": health["status"],
            "label": health["label"],
            "icon": health["icon"],
            "smart_raw": info["smart"],
            "protocol": info["protocol"],
            "physical_name": info["name"],
            "updatedAt": datetime.now().isoformat()
        }

        # Оновлюємо індекс
        index[disk_id]["health"] = health_data
        print(f"→ {health['icon']} {health['label']} → {disk_entry['name']}")
        updated += 1
        print()

    if updated == 0:
        print("Нічого не оновлено")
        sys.exit(0)

    # Зберігаємо
    print(f"⟳ Зберігаємо {updated} оновлень...")
    result = patch_index(index)
    if result and result.get("ok"):
        print(f"✅ Готово! Оновлено {updated} дисків")
        print()
        print("Відкрий DiskMap — статус відображатиметься на картках дисків")
    else:
        print("✗ Помилка збереження")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nПерервано")
        sys.exit(0)
