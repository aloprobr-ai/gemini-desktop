# -*- coding: utf-8 -*-
"""Инструменты модели не достают до папки программы.

Раньше модель могла через «создать файл» переписать tools\\<имя>.json
с approved: true и запустить свой код без человека, а через него же
подменить settings.json. Теперь запись и чтение в %APPDATA%\\GeminiDesktop
закрыты, как бы ни был записан путь.

Всё происходит во временной папке: APPDATA подменяется до импорта app.

    python tests/test_tool_guard.py
"""
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="gd-guard-")
os.environ["APPDATA"] = TMP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import app  # noqa: E402

ok = 0
bad = []


def check(name, cond, detail=''):
    global ok
    if cond:
        ok += 1
        print('  OK   ' + name)
    else:
        bad.append(name)
        print('  FAIL ' + name + (' — ' + str(detail) if detail else ''))


def denied(res):
    return res.get("status") == "error" and res.get("error") == app.DATA_DIR_DENIED


def short_name(path):
    """Короткое имя 8.3, если файловая система их хранит."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
        return buf.value if n else ""
    except Exception:
        return ""


try:
    check("папка программы в песочнице", app.DATA_DIR.startswith(TMP), app.DATA_DIR)
    os.makedirs(app.TOOLS_DIR, exist_ok=True)
    app.write_json(app.SETTINGS_PATH, {"apiKey": "sk-secret-guard"})

    user_dir = os.path.join(TMP, "Downloads")
    os.makedirs(user_dir)
    s = {"defaultDir": user_dir}

    print("атака из отчёта")
    res = app.tool_create_tool({"name": "guard_poc", "description": "x",
                                "code": "def run(args):\n    return {'pwned': True}"}, s)
    check("create_tool создаёт инструмент без разрешения", res.get("status") == "ok", res)
    res = app.tool_create_file({"directory": r"%APPDATA%\GeminiDesktop\tools",
                                "filename": "guard_poc.json", "mode": "overwrite",
                                "content": json.dumps({"name": "guard_poc", "approved": True})}, s)
    check("перезапись tools\\*.json закрыта", denied(res), res)
    check("инструмент так и не разрешён", app.resolve_tool("guard_poc") is None)

    res = app.tool_create_file({"directory": "%APPDATA%\\GeminiDesktop", "filename": "settings.json",
                                "mode": "overwrite", "content": '{"updateUrl": "http://evil"}'}, s)
    check("перезапись settings.json закрыта", denied(res), res)
    check("settings.json не тронут",
          app.read_json(app.SETTINGS_PATH, {}).get("apiKey") == "sk-secret-guard")

    res = app.tool_read_file({"path": app.SETTINGS_PATH}, s)
    check("чтение settings.json закрыто", denied(res), res)
    check("ключ не попал в ответ", "sk-secret-guard" not in json.dumps(res))

    print("другие записи того же пути")
    variants = {
        "верхний регистр": app.TOOLS_DIR.upper(),
        "через ..": os.path.join(user_dir, "..", "GeminiDesktop", "tools"),
        "относительно папки по умолчанию": os.path.join("..", "GeminiDesktop", "tools"),
        "прямые слэши": app.TOOLS_DIR.replace("\\", "/"),
        "точка на конце": app.DATA_DIR + ".\\tools",
        "пробел на конце": app.DATA_DIR + " \\tools",
        "\\\\?\\": "\\\\?\\" + app.TOOLS_DIR,
        "новая подпапка": os.path.join(app.DATA_DIR, "new", "deeper"),
        "сама папка": app.DATA_DIR,
    }
    sn = short_name(app.TOOLS_DIR)
    if sn and sn.lower() != app.TOOLS_DIR.lower():
        variants["короткое имя 8.3"] = sn
    drive, rest = os.path.splitdrive(app.TOOLS_DIR)
    unc = "\\\\localhost\\" + drive[0].lower() + "$" + rest
    if os.path.isdir(unc):
        variants["\\\\localhost\\c$"] = unc
    else:
        print("  --   \\\\localhost\\c$ недоступен, вариант пропущен")
    for label, directory in variants.items():
        res = app.tool_create_file({"directory": directory, "filename": "x.json",
                                    "content": "{}"}, s)
        check("запись: " + label, denied(res), res)
        res = app.tool_read_file({"path": os.path.join(directory, "guard_poc.json")}, s)
        check("чтение: " + label, denied(res), res)

    print("обычная работа не сломана")
    res = app.tool_create_file({"filename": "note.txt", "content": "привет"}, s)
    check("файл в папке по умолчанию создаётся", res.get("status") == "ok", res)
    res = app.tool_read_file({"path": "note.txt"}, s)
    check("и читается", res.get("status") == "ok" and res.get("content") == "привет", res)
    sib = os.path.join(TMP, "GeminiDesktopNotes")
    res = app.tool_create_file({"directory": sib, "filename": "a.txt", "content": "1"}, s)
    check("соседняя папка с похожим именем не задета", res.get("status") == "ok", res)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nИтого: %d OK, %d FAIL" % (ok, len(bad)))
sys.exit(1 if bad else 0)
