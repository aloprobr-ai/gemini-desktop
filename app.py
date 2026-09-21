# -*- coding: utf-8 -*-
"""
Gemini Desktop — настольный клиент.

Чаты, выбор модели, глобальный промт, отдельный промт инструментов
и инструмент создания txt-файлов (по умолчанию — в «Загрузки»).

Поддерживает два бэкенда:
  * openai — любой OpenAI-совместимый шлюз (например, свой agy-gateway);
  * google — Google Generative Language API напрямую.
"""
import codecs
import collections
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid

import webview

APP_NAME = "Gemini Desktop"
APP_VERSION = "1.2.0"

# Откуда берутся обновления: выпуски GitHub. Нужен публичный репозиторий —
# у закрытого тот же адрес отвечает 404, и проверка молча ничего не находит.
GITHUB_REPO = "aloprobr-ai/gemini-desktop"
GITHUB_API = "https://api.github.com"

# Запасной путь: свой шлюз с /up. Задаётся ключом "updateUrl" в settings.json
# и в окне настроек не показывается — это на случай, когда GitHub недоступен
# или сборки раздаются внутри организации.
TOAST_APP_ID = "GeminiDesktop.App"
TOAST_APP_LABEL = "Gemini"   # так подписаны уведомления
AUTOSTART_NAME = "GeminiDesktop"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAX_TOOL_ROUNDS = 6

# Приложенные картинки лежат в JSON чата целиком, поэтому ограничиваем и число,
# и размер: иначе один разговор распухнет до десятков мегабайт.
MAX_IMAGES = 4
MAX_IMAGE_CHARS = 8 * 1024 * 1024
GOOGLE_ROOT = "https://generativelanguage.googleapis.com/v1beta"

DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "GeminiDesktop")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
CHATS_DIR = os.path.join(DATA_DIR, "chats")
INDEX_PATH = os.path.join(CHATS_DIR, "index.json")
LEGACY_CHATS_PATH = os.path.join(DATA_DIR, "chats.json")  # до разбиения по файлам
TOOLS_DIR = os.path.join(DATA_DIR, "tools")               # инструменты, написанные моделью

DEFAULT_GLOBAL_PROMPT = """Ты — полезный ассистент. Отвечай на языке пользователя, по делу и без воды.
Длинные ответы структурируй заголовками и списками. Код — всегда в блоках с указанием языка."""

DEFAULT_TOOLS_PROMPT = """# Инструменты

## create_file
Создаёт текстовый файл на компьютере пользователя.

Параметры:
- filename — имя файла, например «список покупок» или «отчёт.csv».
  Можно с расширением: .txt, .md, .csv, .json, .log. Без него будет .txt.
- content — полное содержимое файла. Без markdown-обёрток и без ```.
- directory — необязательно. downloads, desktop, documents или полный путь
  вида D:\\Работа\\отчёты. Если не указать — файл уйдёт в папку «Загрузки».
- mode — необязательно: create (по умолчанию, не затирает — добавит номер),
  overwrite (перезаписать), append (дописать в конец).

Когда вызывать: просят сохранить, записать, выгрузить текст в файл; или ты сам
видишь, что результат удобнее отдать файлом — длинный список, конспект, код,
инструкция, таблица. Таблицу лучше отдавать в .csv, размеченный текст — в .md.

## read_file
Читает текстовый файл и возвращает его содержимое.

Параметры:
- path — полный путь к файлу, либо имя файла вместе с directory.
- directory — необязательно, как выше.

Когда вызывать: просят посмотреть, прочитать, разобрать, продолжить или
исправить файл. Очень большие файлы возвращаются обрезанными — об этом
будет сказано в результате.

## create_tool
Создаёт новый инструмент, если имеющихся не хватает.

Параметры:
- name — имя латиницей в нижнем регистре: count_lines.
- description — что инструмент делает.
- parameters — JSON Schema аргументов: {"type":"object","properties":{...}}.
- code — Python с функцией run(args), возвращающей словарь. Доступна вся
  стандартная библиотека.

Когда вызывать: задача повторяется или её не решить имеющимися инструментами —
посчитать что-то во множестве файлов, переименовать пачку, собрать сводку.
Не плоди инструменты на разовую просьбу: если хватает create_file или
read_file, обойдись ими.

Готовый инструмент сразу не работает: пользователь должен посмотреть код и
разрешить запуск. Обязательно скажи ему об этом и в двух словах объясни,
что делает код.

## open_folder
Открывает папку в проводнике. Если передать путь к файлу — откроет папку
с этим файлом и выделит его.

Параметры:
- path — папка или файл. Пусто — папка по умолчанию.

Когда вызывать: просят показать, открыть папку, «покажи где лежит».

Как вызывать — выведи блок ровно такого вида и ничего после него:

```tool_call
{"tool": "create_file", "filename": "имя", "content": "текст файла", "directory": ""}
```

После того как придёт результат, коротко подтверди и укажи полный путь.
Не выдумывай путь заранее — бери его из результата вызова."""

# Прежние версии промта инструментов: пока пользователь держит
# один из них нетронутым, подменяем на свежий при запуске.
OLD_TOOLS_PROMPTS = (
    '# Инструменты\n\n## create_txt_file\nСоздаёт текстовый файл на компьютере пользователя.\n\nПараметры:\n- filename — имя файла, например «список покупок». Расширение .txt добавится само.\n- content — полный текст файла. Без markdown-обёрток и без ```.\n- directory — необязательно. downloads, desktop, documents или полный путь\n  вида D:\\Работа\\отчёты. Если не указать — файл уйдёт в папку «Загрузки».\n- mode — необязательно: create (по умолчанию, не затирает — добавит номер),\n  overwrite (перезаписать), append (дописать в конец).\n\nКогда вызывать:\n- просят сохранить, записать, выгрузить текст в файл;\n- говорят «сделай txt», «сохрани в загрузки», «запиши на рабочий стол»;\n- ты сам видишь, что результат удобнее отдать файлом: длинный список,\n  конспект, код, инструкция, таблица.\n\nКак вызывать — выведи блок ровно такого вида и ничего после него:\n\n```tool_call\n{"tool": "create_txt_file", "filename": "имя", "content": "текст файла", "directory": ""}\n```\n\nПосле того как придёт результат, коротко подтверди и укажи полный путь к файлу.\nНе выдумывай путь заранее — бери его из результата вызова.',
    '# Инструменты\n\n## create_file\nСоздаёт текстовый файл на компьютере пользователя.\n\nПараметры:\n- filename — имя файла, например «список покупок» или «отчёт.csv».\n  Можно с расширением: .txt, .md, .csv, .json, .log. Без него будет .txt.\n- content — полное содержимое файла. Без markdown-обёрток и без ```.\n- directory — необязательно. downloads, desktop, documents или полный путь\n  вида D:\\Работа\\отчёты. Если не указать — файл уйдёт в папку «Загрузки».\n- mode — необязательно: create (по умолчанию, не затирает — добавит номер),\n  overwrite (перезаписать), append (дописать в конец).\n\nКогда вызывать: просят сохранить, записать, выгрузить текст в файл; или ты сам\nвидишь, что результат удобнее отдать файлом — длинный список, конспект, код,\nинструкция, таблица. Таблицу лучше отдавать в .csv, размеченный текст — в .md.\n\n## read_file\nЧитает текстовый файл и возвращает его содержимое.\n\nПараметры:\n- path — полный путь к файлу, либо имя файла вместе с directory.\n- directory — необязательно, как выше.\n\nКогда вызывать: просят посмотреть, прочитать, разобрать, продолжить или\nисправить файл. Очень большие файлы возвращаются обрезанными — об этом\nбудет сказано в результате.\n\n## open_folder\nОткрывает папку в проводнике. Если передать путь к файлу — откроет папку\nс этим файлом и выделит его.\n\nПараметры:\n- path — папка или файл. Пусто — папка по умолчанию.\n\nКогда вызывать: просят показать, открыть папку, «покажи где лежит».\n\nКак вызывать — выведи блок ровно такого вида и ничего после него:\n\n```tool_call\n{"tool": "create_file", "filename": "имя", "content": "текст файла", "directory": ""}\n```\n\nПосле того как придёт результат, коротко подтверди и укажи полный путь.\nНе выдумывай путь заранее — бери его из результата вызова.',
)

DEFAULT_SETTINGS = {
    "provider": "openai",
    "baseUrl": "http://127.0.0.1:8080/v1",
    "apiKey": "",
    "googleKey": "",
    "model": "gemini-3.8-flash-medium",
    "globalPrompt": DEFAULT_GLOBAL_PROMPT,
    "toolsPrompt": DEFAULT_TOOLS_PROMPT,
    "temperature": 0.9,
    "maxTokens": 8192,
    "toolsEnabled": True,
    "streaming": True,
    "showThoughts": True,
    "defaultDir": "",
    "proxy": "",
    "theme": "dark",
}

# Шлюз отдаёт вперемешку настоящие модели и псевдонимы вроде agy-fast или gpt-4o,
# которые ведут на те же самые модели. В списке показываем только настоящие,
# а сохранённый ранее псевдоним молча переводим на модель, куда он вёл.
MODEL_ALIASES = {
    "agy": "gemini-3.7-flash-medium",
    "agy-pro": "gemini-3.1-pro-high",
    "agy-fast": "gemini-3.7-flash-low",
    "agy-claude": "claude-sonnet-4-6",
    "gpt-4o": "gemini-3.7-flash-medium",
    "gpt-4o-mini": "gemini-3.7-flash-low",
    "gpt-4.1": "gemini-3.1-pro-high",
    "default": "gemini-3.7-flash-medium",
}

# Уровень «думания» зашит в имя модели последним словом. В интерфейсе мы разделяем
# модель и уровень: 7 моделей в списке + отдельный переключатель уровня.
MODEL_LEVELS = {"high", "medium", "low", "thinking"}
LEVEL_ORDER = ["low", "medium", "high", "thinking"]
MODEL_TITLE_OVERRIDES = {
    "gpt-oss-120b-medium": "GPT-OSS 120B · Medium",
    "gpt-oss-120b": "GPT-OSS 120B",
}

FALLBACK_MODELS = [
    {"id": "gemini-3.8-flash-medium", "title": "Gemini 3.8 Flash · Medium"},
    {"id": "gemini-3.1-pro-high", "title": "Gemini 3.1 Pro · High"},
    {"id": "claude-sonnet-4-6", "title": "Claude Sonnet 4.6"},
]


def resolve_alias(model):
    """Псевдоним -> настоящее имя модели. Незнакомое имя возвращаем как есть."""
    return MODEL_ALIASES.get((model or "").strip(), model)


def pretty_model_title(mid):
    """gemini-3.7-flash-medium -> «Gemini 3.7 Flash · Medium»."""
    if mid in MODEL_TITLE_OVERRIDES:
        return MODEL_TITLE_OVERRIDES[mid]

    parts = mid.split("-")
    level = parts.pop() if parts and parts[-1] in MODEL_LEVELS else None

    # claude-opus-4-6 -> claude opus 4.6: склеиваем разбитые точкой версии
    merged = []
    for part in parts:
        if part.isdigit() and merged and re.fullmatch(r"\d+(\.\d+)*", merged[-1]):
            merged[-1] += "." + part
        else:
            merged.append(part)

    words = []
    for part in merged:
        low = part.lower()
        if low in ("gpt", "oss", "ai"):
            words.append(part.upper())
        elif re.fullmatch(r"\d+(\.\d+)*", part) or re.fullmatch(r"\d+[a-z]+", low):
            words.append(part.upper())
        else:
            words.append(part.capitalize())

    title = " ".join(words)
    return title + " · " + level.capitalize() if level else title


def split_model_level(mid):
    """gemini-3.7-flash-medium -> ('gemini-3.7-flash', 'medium')."""
    parts = (mid or "").split("-")
    if parts and parts[-1] in MODEL_LEVELS:
        return "-".join(parts[:-1]), parts[-1]
    return mid, ""


def group_models(models):
    """Схлопывает варианты одной модели в группу с уровнями думания."""
    groups, order = {}, []
    for m in models:
        base, level = split_model_level(m["id"])
        group = groups.get(base)
        if group is None:
            group = {"base": base, "title": pretty_model_title(base),
                     "levels": [], "isNew": False}
            groups[base] = group
            order.append(base)
        group["levels"].append({"id": m["id"], "level": level,
                                "created": m.get("created", 0),
                                "isNew": bool(m.get("isNew"))})
        if m.get("isNew"):
            group["isNew"] = True

    for base in order:
        groups[base]["levels"].sort(
            key=lambda lv: LEVEL_ORDER.index(lv["level"]) if lv["level"] in LEVEL_ORDER else -1)
    return sorted((groups[b] for b in order), key=lambda g: g["title"])

DIR_HINT = "downloads, desktop, documents или абсолютный путь. Пусто = «Загрузки»."

TOOL_SCHEMAS = [
    {
        "name": "create_file",
        "description": (
            "Создаёт текстовый файл на компьютере пользователя и возвращает полный путь. "
            "Разрешены .txt, .md, .csv, .json, .log; без расширения будет .txt. "
            "Если папка не указана — файл сохраняется в папку «Загрузки»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Имя файла без пути. Можно с расширением .txt, .md, .csv, .json, .log.",
                },
                "content": {
                    "type": "string",
                    "description": "Полное содержимое файла обычным текстом.",
                },
                "directory": {"type": "string", "description": DIR_HINT},
                "mode": {
                    "type": "string",
                    "enum": ["create", "overwrite", "append"],
                    "description": "create — не затирать, overwrite — перезаписать, append — дописать.",
                },
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Читает текстовый файл с компьютера пользователя и возвращает его содержимое. "
            "Большие файлы возвращаются обрезанными."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Полный путь к файлу либо имя файла вместе с directory.",
                },
                "directory": {"type": "string", "description": DIR_HINT},
            },
            "required": ["path"],
        },
    },
    {
        "name": "create_tool",
        "description": (
            "Создаёт новый инструмент, которого ещё нет. Нужен, когда задача повторяется "
            "или её нельзя решить имеющимися инструментами. Код запустится только после "
            "того, как пользователь его посмотрит и разрешит."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Имя латиницей в нижнем регистре, словами через подчёркивание: count_lines.",
                },
                "description": {
                    "type": "string",
                    "description": "Что инструмент делает — эту строку увидит и пользователь, и ты сама потом.",
                },
                "parameters": {
                    "type": "object",
                    "description": "JSON Schema аргументов: {\"type\":\"object\",\"properties\":{...}}.",
                },
                "code": {
                    "type": "string",
                    "description": (
                        "Python-код с функцией run(args), которая возвращает словарь. "
                        "Доступна вся стандартная библиотека. Импорты — внутри функции или сверху."
                    ),
                },
            },
            "required": ["name", "description", "code"],
        },
    },
    {
        "name": "open_folder",
        "description": (
            "Открывает папку в проводнике. Если передан путь к файлу — открывает "
            "папку с ним и выделяет файл."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Папка или файл. Пусто — папка по умолчанию.",
                },
            },
        },
    },
]


# --------------------------------------------------------------------- утилиты

def known_folder(guid):
    """Путь к системной папке через SHGetKnownFolderPath (учитывает перенос папок)."""
    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort),
                    ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    parts = guid.split("-")
    g = GUID()
    g.a = int(parts[0], 16)
    g.b = int(parts[1], 16)
    g.c = int(parts[2], 16)
    for i, byte in enumerate(bytes.fromhex(parts[3] + parts[4])):
        g.d[i] = byte
    ptr = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr)) != 0:
        return None
    path = ptr.value
    ctypes.windll.ole32.CoTaskMemFree(ptr)
    return path


def _folder(guid, fallback):
    try:
        path = known_folder(guid)
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), fallback)


def downloads_dir():
    return _folder("374DE290-123F-4565-9164-39C4925E467B", "Downloads")


def desktop_dir():
    return _folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop")


def documents_dir():
    return _folder("FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents")


# ---------------------------------------------------------------- одна копия

# Крестик у окна прячет приложение в значок, процесс при этом живёт. Если потом
# запустить ярлык, Windows поднимет вторую копию: два значка в трее, два окна
# WebView2 и двойная память. Поэтому запущенная копия слушает маленький сокет
# на 127.0.0.1, а новая стучится в него и, если ответили, показывает окно
# старой и уходит. Номер порта лежит рядом с настройками.
INSTANCE_FILE = os.path.join(DATA_DIR, "instance.port")
INSTANCE_LOCK = "Local\\GeminiDesktop.instance"
_instance_lock = None


def claim_instance():
    """True — мы единственные.

    Опознаём по замку ядра, а не по файлу с портом: файл пишется и читается
    в два приёма, и две копии, запущенные разом, успевают проскочить мимо
    друг друга. Замок берётся одной операцией.
    """
    global _instance_lock
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, INSTANCE_LOCK)
        if not handle:
            return True   # замок не дался — пусть лучше запустится
        if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            return False
        _instance_lock = handle   # держим до конца жизни процесса
        return True
    except Exception as exc:
        blog("PY   замок копий не взялся: " + exc_text(exc))
        return True


def wake_running(show=True):
    """Попросить работающую копию показать окно.

    Сначала стучимся в сокет: тогда та копия покажет окно сама и не собьётся
    со счёта, видно её или нет. Если не вышло — поднимаем окно средствами
    Windows: лучше несогласованный признак видимости, чем человек, у которого
    приложение «не открывается».
    """
    for attempt in range(3):
        try:
            with open(INSTANCE_FILE, "r", encoding="utf-8") as fh:
                port = int(fh.read().strip())
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                sock.sendall(b"show" if show else b"quiet")
                sock.recv(8)
            return True
        except Exception:
            time.sleep(0.4)   # та копия ещё поднимается — подождём и постучим снова
    if not show:
        return False
    return raise_window()


def raise_window():
    """Показать окно чужой копии напрямую, по заголовку."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_NAME)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)        # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as exc:
        blog("PY   окно чужой копии не поднялось: " + exc_text(exc))
        return False


def serve_instance(api):
    """Слушаем стук новых копий и по нему показываем окно."""
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        with open(INSTANCE_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(srv.getsockname()[1]))
    except Exception as exc:
        blog("PY   сторож копий не поднялся: " + exc_text(exc))
        return

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                return
            try:
                data = conn.recv(8)
                conn.sendall(b"ok")
            except Exception:
                data = b""
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            if data.startswith(b"show"):
                blog("PY   стучится вторая копия, показываю окно")
                api.show_window()

    threading.Thread(target=loop, daemon=True).start()


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def version_tuple(text):
    """«1.2.0» -> (1, 2, 0). Непонятное превращается в нули, а не в ошибку."""
    parts = []
    for chunk in re.split(r"[.\-+]", str(text or "").strip().lstrip("vV")):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            break            # «1.2.0-beta» сравниваем как 1.2.0
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def fetch_json(url, timeout=12):
    """GET и разбор JSON. Со своим User-Agent: без него GitHub отвечает отказом."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "GeminiDesktop/" + APP_VERSION,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_update(current=APP_VERSION, gateway=""):
    """Есть ли версия новее. Молчит, если сети нет."""
    if gateway:
        return check_update_gateway(gateway, current)
    return check_update_github(current)


def check_update_github(current=APP_VERSION):
    """Последний выпуск в GitHub.

    Черновики и предварительные выпуски сюда не попадают: /releases/latest
    отдаёт только готовые. Значит, случайно опубликованный черновик не уедет
    людям в виде обновления.
    """
    url = GITHUB_API + "/repos/" + GITHUB_REPO + "/releases/latest"
    try:
        rel = fetch_json(url)
    except Exception as exc:
        return {"update": False, "current": current, "error": exc_text(exc)}
    if not isinstance(rel, dict):
        return {"update": False, "current": current}

    version = str(rel.get("tag_name") or "").lstrip("vV")
    msi = None
    sums = None
    for asset in rel.get("assets") or []:
        name = str(asset.get("name") or "").lower()
        if name.endswith(".msi") and msi is None:
            msi = asset
        elif name.endswith(".sha256") or name == "sha256sums":
            sums = asset
    if not version or msi is None:
        # Выпуск есть, а установщика к нему не приложили — обновлять нечем.
        return {"update": False, "current": current, "version": version}

    # Метку порядка байтов в начале текста GitHub сохраняет как есть — видел
    # такое у чужих выпусков. Без неё первое слово не совпало бы ни с чем.
    notes = str(rel.get("body") or "").lstrip("﻿").strip()
    important = False
    if notes.lower().startswith("[важно]"):
        important = True
        notes = notes[len("[важно]"):].strip()

    return {
        "update": version_tuple(version) > version_tuple(current),
        "version": version,
        "current": current,
        "important": important,
        "notes": notes,
        "size": int(msi.get("size") or 0),
        "sha256": fetch_sha256(sums, str(msi.get("name") or "")),
        "publishedAt": str(rel.get("published_at") or ""),
        "url": str(msi.get("browser_download_url") or ""),
    }


def fetch_sha256(asset, msi_name):
    """Контрольная сумма установщика из приложенного к выпуску файла.

    GitHub сумм не считает, поэтому она кладётся рядом отдельным файлом.
    Не нашли — вернём пустую строку: тогда установщик скачается без сверки.
    Это слабее, но честнее, чем притвориться, что проверка была.
    """
    if not isinstance(asset, dict):
        return ""
    url = str(asset.get("browser_download_url") or "")
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GeminiDesktop/" + APP_VERSION})
        with urllib.request.urlopen(req, timeout=12) as resp:
            text = resp.read(8192).decode("utf-8", "replace")
    except Exception:
        return ""
    # Формат как у sha256sum: «<сумма>  <имя файла>», иногда одна сумма без имени.
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        digest = parts[0].lower()
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            continue
        if len(parts) == 1 or not msi_name or parts[-1].lstrip("*").lower() == msi_name.lower():
            return digest
    return ""


def check_update_gateway(base, current=APP_VERSION):
    """Свой шлюз с /up — на случай, когда GitHub недоступен."""
    url = base.rstrip("/") + "?from=" + urllib.parse.quote(current)
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"update": False, "current": current, "error": exc_text(exc)}
    if not isinstance(data, dict):
        return {"update": False, "current": current}
    data["current"] = current
    return data


def download_update(url, sha256, on_progress=None):
    """Качает установщик во временную папку и сверяет контрольную сумму.

    Сумму проверяем обязательно: файл после этого запускается с правами
    пользователя, и подсунутый по дороге установщик — худшее, что может быть.
    """
    import hashlib

    target = os.path.join(tempfile.gettempdir(), "GeminiDesktop-update.msi")
    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "GeminiDesktop/" + APP_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(target, "wb") as fh:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(int(done * 100 / total))
    except Exception as exc:
        return {"ok": False, "error": exc_text(exc)}

    # Сумму сверяем, если она есть: файл после этого запускается с правами
    # пользователя, и подменённый по дороге установщик — худшее, что может быть.
    if sha256 and digest.hexdigest().lower() != str(sha256).lower():
        try:
            os.remove(target)
        except OSError:
            pass
        return {"ok": False, "error": "Контрольная сумма не сошлась — файл не тот, установка отменена"}

    return {"ok": True, "path": target}


def frozen_exe():
    """Путь к собранному .exe или пусто, если запущено из исходников."""
    return sys.executable if getattr(sys, "frozen", False) else ""


def autostart_state():
    """Стоит ли приложение в автозапуске текущего пользователя."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
        return bool(value)
    except Exception:
        return False


def set_autostart(enabled):
    """Добавляет или убирает запись в автозапуске.

    Пишем только свой ключ в ветке текущего пользователя и только по
    галочке в настройках — сам по себе автозапуск не включается.
    """
    import winreg

    if not enabled:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, AUTOSTART_NAME)
        except FileNotFoundError:
            pass
        return True

    exe = frozen_exe()
    if not exe:
        return False   # из исходников автозапуск прописывать некуда
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
        winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ,
                          '"%s" --tray' % exe)
    return True


def register_toast_name():
    """Имя, которое Windows пишет над всплывающим сообщением.

    Система берёт его из AppUserModelID процесса. Пока идентификатор
    не задан, подставляется имя файла — «Gemini Desktop.exe». Регистрация
    лежит в ветке текущего пользователя и правит только свой же ключ.
    """
    try:
        import winreg

        icon = os.path.join(DATA_DIR, "icon.ico")
        if not os.path.exists(icon):
            # в onefile-сборке ресурсы живут во временной папке и пропадают
            # после выхода, поэтому кладём копию рядом с настройками
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(resource_path("icon.ico"), "rb") as src:
                    with open(icon, "wb") as dst:
                        dst.write(src.read())
            except Exception:
                icon = ""

        path = r"Software\Classes\AppUserModelId\%s" % TOAST_APP_ID
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, TOAST_APP_LABEL)
            if icon:
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon)

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(TOAST_APP_ID)
    except Exception:
        blog("PY   имя для уведомлений задать не вышло")


def read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return fallback


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=chr(10)) as fh:
        fh.write(text)


def now_ms():
    return int(time.time() * 1000)


# --- замер времени старта: пишем в boot.log, чтобы видеть, что тормозит ---
BOOT_T0 = time.perf_counter()
BOOT_LOG = os.path.join(DATA_DIR, "boot.log")
_boot_lock = threading.Lock()


SECRET_FIELDS = ("apiKey", "googleKey")
_secrets = set()
_secrets_lock = threading.Lock()


def remember_secrets(settings):
    """Держим ключи под рукой, чтобы вычищать их из текста наружу."""
    with _secrets_lock:
        _secrets.clear()
        for name in SECRET_FIELDS:
            value = (settings.get(name) or "").strip()
            if len(value) >= 8:      # слишком короткое похоже на заглушку
                _secrets.add(value)


def scrub(text):
    """Ключ не должен попасть ни в лог, ни в сообщение об ошибке.

    Шлюз умеет возвращать присланный ключ в теле ошибки, поэтому чистим
    всё, что уходит из Python наружу, а не только то, что пишем сами.
    """
    text = "" if text is None else str(text)
    with _secrets_lock:
        for value in _secrets:
            if value in text:
                text = text.replace(value, "<ключ скрыт>")
    return text


def exc_text(exc):
    return scrub("%s: %s" % (type(exc).__name__, exc))


def public_settings(settings):
    """Настройки для интерфейса: вместо ключей — только признак «задан»."""
    out = dict(settings)
    for name in SECRET_FIELDS:
        out[name] = ""
        out[name + "Set"] = bool((settings.get(name) or "").strip())
    return out


def blog(msg):
    line = "%7.0f ms  %s\n" % ((time.perf_counter() - BOOT_T0) * 1000, scrub(msg))
    try:
        with _boot_lock:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(BOOT_LOG, "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass


# ------------------------------------------------------------ инструмент: txt

DIR_ALIASES = {
    "downloads": downloads_dir, "download": downloads_dir,
    "загрузки": downloads_dir, "загрузка": downloads_dir,
    "desktop": desktop_dir, "рабочий стол": desktop_dir, "стол": desktop_dir,
    "documents": documents_dir, "docs": documents_dir, "документы": documents_dir,
}

BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# Чем разрешено создавать файлы. Всё остальное приводится к .txt: писать
# на диск .exe или .bat по просьбе модели приложение не должно.
TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log")

# Что готовы читать. Список шире: сюда попадают и файлы, которые пользователь
# сам просит разобрать, но двоичные форматы по-прежнему не пускаем.
READABLE_EXTS = TEXT_EXTS + (".ini", ".cfg", ".conf", ".yml", ".yaml", ".xml",
                             ".html", ".css", ".js", ".py", ".php", ".sql",
                             ".bat", ".ps1", ".sh", ".env", ".gitignore", "")

READ_LIMIT = 200 * 1024   # больше в диалог всё равно не влезет


def safe_filename(name):
    name = (name or "").strip().strip(".")
    name = os.path.basename(name.replace("/", "\\"))
    name = BAD_CHARS.sub("_", name).strip()
    if not name:
        name = "gemini"
    root, ext = os.path.splitext(name)
    if ext.lower() not in TEXT_EXTS:
        root, ext = name, ".txt"
    return (root[:120].strip() or "gemini") + ext


def resolve_dir(raw, settings):
    raw = (raw or "").strip().strip('"')
    key = raw.lower()
    if not key:
        return settings.get("defaultDir") or downloads_dir()
    if key in DIR_ALIASES:
        return DIR_ALIASES[key]()
    expanded = os.path.expandvars(os.path.expanduser(raw))
    if os.path.isabs(expanded):
        return expanded
    return os.path.join(settings.get("defaultDir") or downloads_dir(), expanded)


def unique_path(path):
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s (%d)%s" % (root, i, ext)):
        i += 1
    return "%s (%d)%s" % (root, i, ext)


def tool_create_file(args, settings):
    filename = safe_filename(args.get("filename"))
    content = args.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, indent=2)
    mode = str(args.get("mode") or "create").lower()
    directory = resolve_dir(args.get("directory"), settings)

    try:
        os.makedirs(directory, exist_ok=True)
    except Exception as exc:
        return {"status": "error", "error": "Не удалось создать папку %s: %s" % (directory, exc)}

    path = os.path.join(directory, filename)
    if mode == "append" and os.path.exists(path):
        open_mode = "a"
    elif mode == "overwrite":
        open_mode = "w"
    else:
        open_mode = "w"
        path = unique_path(path)

    try:
        with open(path, open_mode, encoding="utf-8", newline="\r\n") as fh:
            if open_mode == "a":
                fh.write("\n")
            fh.write(content.replace("\r\n", "\n"))
    except Exception as exc:
        return {"status": "error", "error": "Не удалось записать файл: %s" % exc}

    return {
        "status": "ok",
        "path": path,
        "filename": os.path.basename(path),
        "directory": directory,
        "bytes": os.path.getsize(path),
        "mode": mode,
    }


def resolve_file_path(raw, settings, directory=None):
    """Путь к существующему файлу: либо абсолютный, либо имя + папка."""
    raw = (raw or "").strip().strip('"')
    if not raw:
        return ""
    expanded = os.path.expandvars(os.path.expanduser(raw))
    if os.path.isabs(expanded):
        return expanded
    return os.path.join(resolve_dir(directory, settings), expanded)


def tool_read_file(args, settings):
    path = resolve_file_path(args.get("path"), settings, args.get("directory"))
    if not path:
        return {"status": "error", "error": "Не указан путь к файлу"}
    if not os.path.isfile(path):
        return {"status": "error", "error": "Файл не найден: %s" % path}

    ext = os.path.splitext(path)[1].lower()
    if ext not in READABLE_EXTS:
        return {"status": "error",
                "error": "Такие файлы читать нельзя: %s. Только текстовые." % (ext or "без расширения")}

    size = os.path.getsize(path)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(READ_LIMIT + 1)
    except Exception as exc:
        return {"status": "error", "error": "Не удалось прочитать файл: %s" % exc}

    if b"\x00" in raw[:4096]:
        return {"status": "error", "error": "Это двоичный файл, а не текст: %s" % path}

    truncated = len(raw) > READ_LIMIT
    text = raw[:READ_LIMIT].decode("utf-8", "replace")
    if truncated:
        text = text[:text.rfind("\n") + 1] or text

    return {
        "status": "ok",
        "path": path,
        "filename": os.path.basename(path),
        "directory": os.path.dirname(path),
        "bytes": size,
        "truncated": truncated,
        "content": text,
    }


def tool_open_folder(args, settings):
    raw = (args.get("path") or "").strip().strip('"')
    if raw:
        target = resolve_file_path(raw, settings)
    else:
        target = settings.get("defaultDir") or downloads_dir()

    if os.path.isfile(target):
        folder, select = os.path.dirname(target), target
    else:
        folder, select = target, ""

    if not os.path.isdir(folder):
        return {"status": "error", "error": "Папка не найдена: %s" % folder}

    try:
        if select:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(select)])
        else:
            os.startfile(folder)
    except Exception as exc:
        return {"status": "error", "error": "Не удалось открыть папку: %s" % exc}

    return {"status": "ok", "path": select or folder, "directory": folder,
            "opened": True}


# Сколько ждём чужой код и сколько принимаем в ответ. Инструмент, который
# думает дольше минуты или возвращает мегабайты, для диалога бесполезен.
CUSTOM_TIMEOUT = 60
CUSTOM_OUTPUT_LIMIT = 100 * 1024

CUSTOM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")

CUSTOM_TEMPLATE = '''# -*- coding: utf-8 -*-
"""%(description)s

Инструмент создан моделью %(created)s.
Запускается отдельным процессом; всё, что вернёт run(), уходит обратно в диалог.
"""


%(code)s
'''


def tool_meta_path(name):
    return os.path.join(TOOLS_DIR, name + ".json")


def tool_code_path(name):
    return os.path.join(TOOLS_DIR, name + ".py")


def load_custom_tools():
    """Все написанные моделью инструменты: и ждущие разрешения, и рабочие."""
    out = []
    try:
        names = sorted(os.listdir(TOOLS_DIR))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        meta = read_json(os.path.join(TOOLS_DIR, fn), None)
        if isinstance(meta, dict) and meta.get("name"):
            meta["code"] = read_text(tool_code_path(meta["name"]))
            out.append(meta)
    return out


def custom_schemas():
    """Схемы только разрешённых инструментов — остальные модели не видны."""
    schemas = []
    for meta in load_custom_tools():
        if not meta.get("approved"):
            continue
        schemas.append({
            "name": meta["name"],
            "description": meta.get("description") or meta["name"],
            "parameters": meta.get("parameters") or {"type": "object", "properties": {}},
        })
    return schemas


def all_schemas():
    return TOOL_SCHEMAS + custom_schemas()


def resolve_tool(name):
    """Встроенный инструмент, свой разрешённый — или None."""
    if name in TOOL_IMPL:
        return TOOL_IMPL[name]
    meta = read_json(tool_meta_path(name or ""), None)
    if isinstance(meta, dict) and meta.get("approved"):
        return lambda args, settings: run_custom_tool(name, args)
    return None


def run_custom_tool(name, args):
    """Чужой код запускаем отдельным процессом.

    Так его можно оборвать по таймеру, а падение не утащит с собой окно.
    В собранном приложении второй экземпляр себя же запускается с --run-tool.
    """
    path = tool_code_path(name)
    if not os.path.exists(path):
        return {"status": "error", "error": "Инструмент %s не найден" % name}

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--run-tool", path]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), "--run-tool", path]

    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(args or {}, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=CUSTOM_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"status": "error",
                "error": "Инструмент %s не уложился в %d с и был остановлен" % (name, CUSTOM_TIMEOUT)}
    except Exception as exc:
        return {"status": "error", "error": exc_text(exc)}

    out = (proc.stdout or b"")[:CUSTOM_OUTPUT_LIMIT].decode("utf-8", "replace").strip()
    if not out:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        return {"status": "error", "error": err[-600:] or "Инструмент ничего не вернул"}
    try:
        data = json.loads(out)
    except Exception:
        return {"status": "ok", "result": out[:4000]}
    return data if isinstance(data, dict) else {"status": "ok", "result": data}


def tool_create_tool(args, settings):
    """Модель описывает новый инструмент; работать он начнёт после разрешения."""
    name = (args.get("name") or "").strip().lower()
    if not CUSTOM_NAME_RE.match(name):
        return {"status": "error",
                "error": "Имя должно быть латиницей в нижнем регистре, 3–40 символов: например count_lines"}
    if name in TOOL_IMPL:
        return {"status": "error", "error": "Инструмент %s уже встроен, выберите другое имя" % name}

    code = (args.get("code") or "").strip()
    if "def run(" not in code:
        return {"status": "error",
                "error": "В code должна быть функция run(args), возвращающая словарь"}

    params = args.get("parameters")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = None
    if not isinstance(params, dict) or params.get("type") != "object":
        params = {"type": "object", "properties": {}}

    existed = os.path.exists(tool_meta_path(name))
    meta = {
        "name": name,
        "description": (args.get("description") or "").strip() or name,
        "parameters": params,
        "approved": False,          # пока человек не посмотрел код — не запускается
        "createdAt": now_ms(),
    }
    try:
        os.makedirs(TOOLS_DIR, exist_ok=True)
        write_text(tool_code_path(name), CUSTOM_TEMPLATE % {
            "description": meta["description"].replace(chr(34) * 3, "'"),
            "created": time.strftime("%d.%m.%Y"),
            "code": code,
        })
        write_json(tool_meta_path(name), meta)
    except Exception as exc:
        return {"status": "error", "error": exc_text(exc)}

    return {
        "status": "ok",
        "pending": True,
        "name": name,
        "description": meta["description"],
        "replaced": existed,
        "path": tool_code_path(name),
        "note": ("Инструмент сохранён, но ещё не работает: пользователь должен "
                 "посмотреть код и разрешить запуск. Скажи ему об этом."),
    }


TOOL_IMPL = {
    "create_file": tool_create_file,
    "create_tool": tool_create_tool,
    "read_file": tool_read_file,
    "open_folder": tool_open_folder,
    # как называлось раньше — старые чаты и привыкшие модели не должны ломаться
    "create_txt_file": tool_create_file,
}

TOOL_BLOCK_RE = re.compile(
    r"```(?:tool_call|json)?\s*(\{(?:[^`]|`(?!``))*?\"tool\"\s*:\s*\"[a-z_]+\"(?:[^`]|`(?!``))*?\})\s*```",
    re.S | re.I,
)


def extract_text_tool_calls(text):
    """Достаёт вызовы инструментов из текстового протокола. Возвращает (чистый текст, вызовы)."""
    calls = []
    if not text or "tool" not in text:
        return text, calls

    def repl(match):
        try:
            data = json.loads(match.group(1))
        except Exception:
            return match.group(0)
        name = data.pop("tool", None)
        if resolve_tool(name) is None:
            return match.group(0)
        args = data.get("args") if isinstance(data.get("args"), dict) else data
        calls.append({"name": name, "args": args})
        return ""

    cleaned = TOOL_BLOCK_RE.sub(repl, text)
    return cleaned.strip(), calls


# ----------------------------------------------------------------------- сеть

def build_opener(proxy):
    proxy = (proxy or "").strip()
    if not proxy:
        return urllib.request.build_opener()
    if proxy.startswith("socks"):
        try:
            import socks
            from sockshandler import SocksiPyHandler
        except Exception:
            raise RuntimeError("Для SOCKS-прокси нужен пакет PySocks")
        p = urllib.parse.urlparse(proxy)
        kind = socks.SOCKS4 if p.scheme.startswith("socks4") else socks.SOCKS5
        return urllib.request.build_opener(
            SocksiPyHandler(kind, p.hostname, p.port or 1080,
                            username=p.username, password=p.password))
    if "://" not in proxy:
        proxy = "http://" + proxy
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def iter_sse_lines(resp):
    """Строки SSE-потока с корректной склейкой кусков.

    Читать ответ построчно нельзя: кусок приходит по мере готовности и может
    оборваться посреди многобайтового символа. Тогда каждая его половина
    декодируется в «замену» и в тексте появляется «на рабочем ??толе».
    Инкрементальный декодер держит хвост до следующего куска.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    read = getattr(resp, "read1", None) or resp.read
    buf = ""
    while True:
        chunk = read(8192)
        if not chunk:
            break
        buf += decoder.decode(chunk)
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            yield line
    buf += decoder.decode(b"", True)
    if buf:
        yield buf


def http_error_text(exc):
    return scrub(_http_error_text(exc))


def _http_error_text(exc):
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:
        return "HTTP %s" % getattr(exc, "code", "?")
    try:
        err = json.loads(raw).get("error", {})
        msg = err.get("message") or raw
        return ("HTTP %s: %s" % (exc.code, msg)).strip()
    except Exception:
        if "<html" in raw.lower():
            return ("HTTP %s — сервер вернул страницу ошибки, а не ответ API. "
                    "Похоже, шлюз не смог обработать запрос." % exc.code)
        return "HTTP %s: %s" % (exc.code, raw[:300])


# ------------------------------------------------------------------ бэкенды

class OpenAIBackend:
    """OpenAI-совместимый шлюз: /chat/completions + /models."""

    def __init__(self, settings):
        self.s = settings
        self.base = (settings.get("baseUrl") or "").rstrip("/")
        self.key = (settings.get("apiKey") or "").strip()

    def headers(self):
        # Шлюз agy держит диалог у себя и без X-Session-Id определяет сессию
        # по первому сообщению. Два похожих запроса попадают в одну беседу,
        # и на второй он отвечает «Чем могу помочь?» вместо ответа. Историю
        # ведём мы сами, поэтому на каждый запрос просим чистую сессию.
        return {
            "Authorization": "Bearer " + self.key,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "text/event-stream",
            "X-Session-Id": uuid.uuid4().hex,
        }

    def usage(self):
        req = urllib.request.Request(self.base + "/usage", headers={
            "Authorization": "Bearer " + self.key})
        with build_opener(self.s.get("proxy")).open(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def models(self):
        req = urllib.request.Request(self.base + "/models", headers={
            "Authorization": "Bearer " + self.key})
        with build_opener(self.s.get("proxy")).open(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = []
        for item in data.get("data", []):
            mid = item.get("id")
            if mid and mid not in MODEL_ALIASES:
                out.append({"id": mid, "title": pretty_model_title(mid),
                            # когда модель появилась на шлюзе; по ней отмечаем новинки
                            "created": int(item.get("created") or 0)})
        out.sort(key=lambda m: m["title"])
        return out

    def build_messages(self, chat, system_text):
        messages = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        for msg in chat["messages"]:
            if msg["role"] == "user":
                images = msg.get("images") or []
                if images:
                    # С картинками содержимое становится списком частей —
                    # так его принимает и наш шлюз, и обычный OpenAI.
                    parts = []
                    if msg.get("text"):
                        parts.append({"type": "text", "text": msg["text"]})
                    for uri in images:
                        parts.append({"type": "image_url", "image_url": {"url": uri}})
                    messages.append({"role": "user", "content": parts})
                else:
                    messages.append({"role": "user", "content": msg["text"]})
            else:
                for call in msg.get("calls", []):
                    if call.get("native"):
                        messages.append({
                            "role": "assistant",
                            "tool_calls": [{
                                "id": call["id"],
                                "type": "function",
                                "function": {"name": call["name"],
                                             "arguments": json.dumps(call["args"], ensure_ascii=False)},
                            }],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(call["result"], ensure_ascii=False),
                        })
                    else:
                        messages.append({
                            "role": "assistant",
                            "content": "```tool_call\n%s\n```" % json.dumps(
                                dict({"tool": call["name"]}, **call["args"]), ensure_ascii=False),
                        })
                        messages.append({
                            "role": "user",
                            "content": "Результат инструмента %s: %s" % (
                                call["name"], json.dumps(call["result"], ensure_ascii=False)),
                        })
                if msg.get("text"):
                    messages.append({"role": "assistant", "content": msg["text"]})
        return messages

    def payload(self, messages, model, stream):
        body = {
            "model": model,
            "messages": messages,
            "temperature": float(self.s.get("temperature", 0.9)),
            "max_tokens": int(self.s.get("maxTokens", 8192)),
        }
        if stream:
            body["stream"] = True
        if self.s.get("toolsEnabled"):
            body["tools"] = [{"type": "function", "function": t} for t in all_schemas()]
        return body

    def stream(self, model, messages, on_text, on_thought, should_stop):
        """Возвращает (текст, размышления, нативные вызовы, ошибка)."""
        use_stream = bool(self.s.get("streaming", True))
        body = json.dumps(self.payload(messages, model, use_stream), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + "/chat/completions", data=body,
                                     method="POST", headers=self.headers())
        text, thoughts, tool_acc = [], [], {}
        full_text = None   # шлюз досылает чистый текст: CLI рвёт буквы в дельтах
        try:
            with build_opener(self.s.get("proxy")).open(req, timeout=600) as resp:
                ctype = (resp.headers.get("content-type") or "").lower()
                if not use_stream or "event-stream" not in ctype:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                    choice = (data.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    content = message.get("content") or ""
                    if isinstance(content, list):
                        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                    if content:
                        on_text(content)
                        text.append(content)
                    reasoning = message.get("reasoning_content") or message.get("reasoning")
                    if reasoning:
                        on_thought(reasoning)
                        thoughts.append(reasoning)
                    for i, tc in enumerate(message.get("tool_calls") or []):
                        fn = tc.get("function") or {}
                        tool_acc[i] = {"id": tc.get("id") or ("call_%d" % i),
                                       "name": fn.get("name") or "",
                                       "args": fn.get("arguments") or ""}
                else:
                    for raw in iter_sse_lines(resp):
                        if should_stop():
                            break
                        line = raw.strip()
                        if not line.startswith("data:"):
                            continue
                        chunk_raw = line[5:].strip()
                        if not chunk_raw or chunk_raw == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(chunk_raw)
                        except Exception:
                            continue
                        if chunk.get("error"):
                            return ("".join(text), "".join(thoughts), [],
                                    str(chunk["error"].get("message") or chunk["error"]))
                        if chunk.get("agy_full_text") is not None:
                            full_text = chunk["agy_full_text"]
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            piece = delta.get("content")
                            if isinstance(piece, list):
                                piece = "".join(p.get("text", "") for p in piece if isinstance(p, dict))
                            if piece:
                                on_text(piece)
                                text.append(piece)
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                            if reasoning:
                                on_thought(reasoning)
                                thoughts.append(reasoning)
                            for tc in delta.get("tool_calls") or []:
                                idx = tc.get("index", 0)
                                slot = tool_acc.setdefault(
                                    idx, {"id": "", "name": "", "args": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] = fn["name"]
                                if fn.get("arguments"):
                                    slot["args"] += fn["arguments"]
        except urllib.error.HTTPError as exc:
            return "".join(text), "".join(thoughts), [], http_error_text(exc)
        except Exception as exc:
            return "".join(text), "".join(thoughts), [], exc_text(exc)

        calls = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            if not slot["name"]:
                continue
            try:
                args = json.loads(slot["args"] or "{}")
            except Exception:
                args = {}
            calls.append({"id": slot["id"] or ("call_%d" % idx),
                          "name": slot["name"], "args": args, "native": True})
        final = full_text if full_text is not None else "".join(text)
        return final, "".join(thoughts), calls, None


class GoogleBackend:
    """Google Generative Language API (streamGenerateContent)."""

    def __init__(self, settings):
        self.s = settings
        self.key = (settings.get("googleKey") or "").strip()

    def models(self):
        req = urllib.request.Request(GOOGLE_ROOT + "/models?pageSize=200",
                                     headers={"x-goog-api-key": self.key})
        with build_opener(self.s.get("proxy")).open(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = []
        for item in data.get("models", []):
            if "generateContent" not in item.get("supportedGenerationMethods", []):
                continue
            name = item.get("name", "").replace("models/", "")
            if "embedding" in name or "aqa" in name:
                continue
            out.append({"id": name, "title": item.get("displayName") or name})
        return out

    def build_messages(self, chat, system_text):
        contents = []
        for msg in chat["messages"]:
            if msg["role"] == "user":
                parts = []
                if msg.get("text"):
                    parts.append({"text": msg["text"]})
                for uri in (msg.get("images") or []):
                    head, _, data = uri.partition(",")
                    mime = head[5:].split(";")[0] or "image/jpeg"
                    parts.append({"inlineData": {"mimeType": mime, "data": data}})
                contents.append({"role": "user", "parts": parts or [{"text": ""}]})
            else:
                for call in msg.get("calls", []):
                    contents.append({"role": "model", "parts": [
                        {"functionCall": {"name": call["name"], "args": call["args"]}}]})
                    contents.append({"role": "user", "parts": [
                        {"functionResponse": {"name": call["name"], "response": call["result"]}}]})
                if msg.get("text"):
                    contents.append({"role": "model", "parts": [{"text": msg["text"]}]})
        return {"contents": contents, "system": system_text}

    def stream(self, model, messages, on_text, on_thought, should_stop):
        payload = {
            "contents": messages["contents"],
            "generationConfig": {
                "temperature": float(self.s.get("temperature", 0.9)),
                "maxOutputTokens": int(self.s.get("maxTokens", 8192)),
            },
        }
        if messages.get("system"):
            payload["systemInstruction"] = {"parts": [{"text": messages["system"]}]}
        if self.s.get("toolsEnabled"):
            payload["tools"] = [{"functionDeclarations": all_schemas()}]
        if self.s.get("showThoughts") and ("2.5" in model or "3." in model):
            payload["generationConfig"]["thinkingConfig"] = {"includeThoughts": True}

        url = "%s/models/%s:streamGenerateContent?alt=sse" % (GOOGLE_ROOT, urllib.parse.quote(model))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-api-key": self.key,
        })

        text, thoughts, calls = [], [], []
        try:
            with build_opener(self.s.get("proxy")).open(req, timeout=600) as resp:
                for raw in iter_sse_lines(resp):
                    if should_stop():
                        break
                    line = raw.strip()
                    if not line.startswith("data:"):
                        continue
                    chunk_raw = line[5:].strip()
                    if not chunk_raw:
                        continue
                    try:
                        chunk = json.loads(chunk_raw)
                    except Exception:
                        continue
                    for cand in chunk.get("candidates", []):
                        for part in cand.get("content", {}).get("parts", []):
                            if "functionCall" in part:
                                fc = part["functionCall"]
                                calls.append({"id": "call_%d" % len(calls),
                                              "name": fc.get("name"),
                                              "args": fc.get("args") or {},
                                              "native": True})
                            elif "text" in part:
                                if part.get("thought"):
                                    on_thought(part["text"])
                                    thoughts.append(part["text"])
                                else:
                                    on_text(part["text"])
                                    text.append(part["text"])
        except urllib.error.HTTPError as exc:
            return "".join(text), "".join(thoughts), [], http_error_text(exc)
        except Exception as exc:
            return "".join(text), "".join(thoughts), [], exc_text(exc)
        return "".join(text), "".join(thoughts), calls, None


class Tray:
    """Значок в области уведомлений и всплывающие сообщения.

    Живёт в своём потоке: у pystray собственный цикл сообщений Windows.
    Если библиотека недоступна, приложение работает как раньше, просто
    без значка — поэтому все вызовы молча переживают отсутствие иконки.
    """

    def __init__(self, api):
        self.api = api
        self.icon = None

    def start(self):
        try:
            import pystray
            from PIL import Image
        except Exception:
            blog("PY   трей недоступен: нет pystray/Pillow")
            return
        try:
            image = Image.open(resource_path("icon.ico"))
        except Exception:
            return

        menu = pystray.Menu(
            pystray.MenuItem("Открыть", lambda *_: self.api.show_window(), default=True),
            pystray.MenuItem("Выход", lambda *_: self.api.quit_app()),
        )
        self.icon = pystray.Icon("GeminiDesktop", image, APP_NAME, menu)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.icon.run()
        except Exception:
            blog("PY   трей не запустился")

    def notify(self, title, message):
        if not self.icon:
            return
        try:
            self.icon.notify(message or " ", title)
        except Exception:
            pass

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


def make_backend(settings):
    if settings.get("provider") == "google":
        return GoogleBackend(settings)
    return OpenAIBackend(settings)


# ------------------------------------------------------------ мост JS <-> Python

class Api:
    """Хранилище держит переписку по файлу на чат.

    При запуске читается только index.json с названиями — сама переписка
    поднимается с диска, когда чат открывают. Сохранение тоже задевает
    один файл, а не всю историю целиком.
    """

    CACHE_LIMIT = 12   # сколько открытых чатов держим в памяти

    def __init__(self):
        self._window = None
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings.update(read_json(SETTINGS_PATH, {}))
        remember_secrets(self.settings)
        self.lock = threading.Lock()
        self.cancelled = set()
        self.busy = set()

        self.index = read_json(INDEX_PATH, [])
        self._cache = collections.OrderedDict()
        self._tray = None
        self._visible = True    # окно на виду: свёрнутое и спрятанное — нет
        self._last_model_ids = []
        self._quitting = False
        self._fullscreen = False
        self._migrate()

    # ---------- миграция

    def _migrate(self):
        """Чиним состояние, оставшееся от прошлых версий."""
        self.settings["model"] = resolve_alias(self.settings.get("model"))
        self._migrate_tools_prompt()
        self._migrate_legacy_storage()

        changed = False
        for meta in self.index:
            real = resolve_alias(meta.get("model"))
            if real != meta.get("model"):
                meta["model"] = real
                changed = True
        if changed:
            self._save_index()

    def _migrate_tools_prompt(self):
        """Промт инструментов хранится копией в настройках.

        Пока пользователь его не менял, подменяем на свежий: иначе про новые
        инструменты модель просто не узнает. Свой текст не трогаем.
        """
        saved = (self.settings.get("toolsPrompt") or "").strip()
        if saved and saved not in [p.strip() for p in OLD_TOOLS_PROMPTS]:
            return
        if saved == DEFAULT_TOOLS_PROMPT.strip():
            return
        self.settings["toolsPrompt"] = DEFAULT_TOOLS_PROMPT
        write_json(SETTINGS_PATH, self.settings)

    def _migrate_legacy_storage(self):
        """Старый монолитный chats.json раскладываем по файлам."""
        if not os.path.exists(LEGACY_CHATS_PATH):
            return
        old = read_json(LEGACY_CHATS_PATH, [])
        known = {m["id"] for m in self.index}
        for chat in old:
            if not chat.get("messages") or chat.get("id") in known:
                continue
            chat["model"] = resolve_alias(chat.get("model"))
            write_json(self._chat_path(chat["id"]), chat)
            self.index.append(self._meta(chat))
        self._sort_index()
        self._save_index()
        # исходник не удаляем, а отодвигаем: вдруг миграция прошла криво
        try:
            os.replace(LEGACY_CHATS_PATH, LEGACY_CHATS_PATH + ".migrated")
        except Exception:
            pass
        blog("PY   перенесено чатов из старого хранилища: %d" % len(old))

    # ---------- события

    def emit(self, event):
        if not self._window:
            return
        try:
            self._window.evaluate_js(
                "window.__ev(%s)" % json.dumps(json.dumps(event, ensure_ascii=False)))
        except Exception:
            pass

    # ---------- хранилище

    def _chat_path(self, chat_id):
        return os.path.join(CHATS_DIR, "%s.json" % chat_id)

    def _sort_index(self):
        """Закреплённые наверху, внутри каждой группы — свежие первыми."""
        self.index.sort(key=lambda m: (0 if m.get("pinned") else 1,
                                       -int(m.get("updatedAt") or 0)))

    def _save_index(self):
        with self.lock:
            write_json(INDEX_PATH, self.index)

    def _meta(self, chat):
        return {"id": chat["id"], "title": chat.get("title") or "Новый чат",
                "updatedAt": chat.get("updatedAt", 0),
                "pinned": bool(chat.get("pinned")),
                "model": chat.get("model", self.settings["model"])}

    def _find_meta(self, chat_id):
        for meta in self.index:
            if meta["id"] == chat_id:
                return meta
        return None

    def _trim_cache(self):
        while len(self._cache) > self.CACHE_LIMIT:
            for cid in list(self._cache):
                if cid not in self.busy:       # чат с идущей генерацией не трогаем
                    del self._cache[cid]
                    break
            else:
                break

    def _find(self, chat_id):
        """Возвращает полный чат, подняв его с диска при необходимости."""
        chat = self._cache.get(chat_id)
        if chat is not None:
            self._cache.move_to_end(chat_id)
            return chat
        if not self._find_meta(chat_id):
            return None
        chat = read_json(self._chat_path(chat_id), None)
        if chat is None:
            return None
        self._cache[chat_id] = chat
        self._trim_cache()
        return chat

    def _persist_chat(self, chat):
        """Пишет один чат и обновляет его строчку в индексе."""
        with self.lock:
            write_json(self._chat_path(chat["id"]), chat)
        meta = self._find_meta(chat["id"])
        fresh = self._meta(chat)
        if meta is None:
            self.index.append(fresh)
        else:
            meta.update(fresh)
        # сортируем в любом случае: новый чат тоже не должен вставать
        # выше закреплённых
        self._sort_index()
        self._save_index()

    def _forget_chat(self, chat_id):
        self._cache.pop(chat_id, None)
        try:
            os.remove(self._chat_path(chat_id))
        except OSError:
            pass

    # ---------- публичное API

    def log(self, msg):
        blog("JS   " + str(msg))
        return True

    def bootstrap(self):
        blog("PY   bootstrap() вызван")
        return {
            "settings": public_settings(self.settings),
            "chats": self.index,          # только названия, без переписки
            "downloads": downloads_dir(),
            "dataDir": DATA_DIR,
            "version": APP_VERSION,
            "fallbackModels": FALLBACK_MODELS,
        }

    def save_settings(self, patch):
        patch = dict(patch or {})
        for name in SECRET_FIELDS:
            patch.pop(name + "Set", None)
            if name in patch:
                value = (patch[name] or "").strip()
                if value:
                    patch[name] = value
                else:
                    # интерфейс ключа не видит и шлёт пустое поле, когда его
                    # не трогали: это «оставить как есть», а не «стереть»
                    patch.pop(name)
        self.settings.update(patch)
        write_json(SETTINGS_PATH, self.settings)
        remember_secrets(self.settings)
        return public_settings(self.settings)

    def clear_key(self, which):
        """Стереть ключ можно только явной кнопкой в настройках."""
        name = "googleKey" if which == "google" else "apiKey"
        self.settings[name] = ""
        write_json(SETTINGS_PATH, self.settings)
        remember_secrets(self.settings)
        return public_settings(self.settings)

    def app_version(self):
        return {"version": APP_VERSION}

    def check_update(self):
        # Свой шлюз, если он вписан в settings.json, иначе выпуски GitHub.
        return check_update(gateway=str(self.settings.get("updateUrl") or "").strip())

    def install_update(self, url, sha256):
        """Качает установщик, сверяет сумму и запускает его, закрывая приложение."""
        res = download_update(url, sha256,
                              lambda pct: self.emit({"type": "update_progress", "pct": pct}))
        if not res.get("ok"):
            return res
        try:
            subprocess.Popen(["msiexec", "/i", res["path"]])
        except Exception as exc:
            return {"ok": False, "error": exc_text(exc)}
        threading.Timer(1.5, self.quit_app).start()
        return {"ok": True}

    def get_autostart(self):
        return {"on": autostart_state(), "available": bool(frozen_exe())}

    def set_autostart(self, on):
        if not frozen_exe():
            return {"ok": False, "available": False,
                    "error": "Автозапуск работает только у собранного приложения"}
        try:
            set_autostart(bool(on))
        except Exception as exc:
            return {"ok": False, "available": True, "error": exc_text(exc)}
        return {"ok": True, "available": True, "on": autostart_state()}

    def list_tools(self):
        """Свои инструменты для настроек: с кодом, чтобы было что посмотреть."""
        return {"ok": True, "tools": load_custom_tools()}

    def approve_tool(self, name, approved):
        meta = read_json(tool_meta_path(name or ""), None)
        if not isinstance(meta, dict):
            return {"ok": False, "error": "Инструмент не найден"}
        meta["approved"] = bool(approved)
        meta.pop("code", None)
        write_json(tool_meta_path(meta["name"]), meta)
        return {"ok": True, "approved": meta["approved"]}

    def delete_tool(self, name):
        meta = read_json(tool_meta_path(name or ""), None)
        if not isinstance(meta, dict):
            return {"ok": False, "error": "Инструмент не найден"}
        for path in (tool_meta_path(meta["name"]), tool_code_path(meta["name"])):
            try:
                os.remove(path)
            except OSError:
                pass
        return {"ok": True}

    def default_prompt(self, which):
        return DEFAULT_GLOBAL_PROMPT if which == "global" else DEFAULT_TOOLS_PROMPT

    def new_chat(self):
        chat = {"id": uuid.uuid4().hex[:12], "title": "Новый чат",
                "model": self.settings["model"], "createdAt": now_ms(),
                "updatedAt": now_ms(), "messages": []}
        self._cache[chat["id"]] = chat
        self._persist_chat(chat)
        return chat

    def get_chat(self, chat_id):
        return self._find(chat_id)

    def delete_chat(self, chat_id):
        self.index = [m for m in self.index if m["id"] != chat_id]
        self._forget_chat(chat_id)
        self._save_index()
        return True

    def pin_chat(self, chat_id, pinned):
        # метку держим в самом файле чата, иначе она потеряется при любом
        # пересборе индекса; строчка в индексе — лишь отражение файла
        chat = self._find(chat_id)
        if not chat:
            return {"ok": False}
        chat["pinned"] = bool(pinned)
        self._persist_chat(chat)
        return {"ok": True, "chats": self.index}

    def rename_chat(self, chat_id, title):
        chat = self._find(chat_id)
        if chat:
            chat["title"] = (title or "").strip() or "Новый чат"
            self._persist_chat(chat)
        return True

    def clear_all(self):
        for meta in list(self.index):
            self._forget_chat(meta["id"])
        self.index = []
        self._save_index()
        return True

    def set_chat_model(self, chat_id, model):
        model = resolve_alias(model)
        chat = self._find(chat_id)
        if chat:
            chat["model"] = model
            self._persist_chat(chat)
        self.settings["model"] = model
        write_json(SETTINGS_PATH, self.settings)
        return True

    def stop(self, chat_id):
        self.cancelled.add(chat_id)
        return True

    # ---------- система

    def open_path(self, path):
        try:
            if os.path.exists(path):
                os.startfile(path)
                return True
        except Exception:
            pass
        return False

    def reveal(self, path):
        try:
            if os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                return True
        except Exception:
            pass
        return False

    # ---------- окно и трей

    def set_visible(self, flag):
        """JS сообщает, видно ли окно: свернули, развернули, переключили вкладку."""
        self._visible = bool(flag)
        return True

    def show_window(self):
        self._visible = True
        win = self._window
        if not win:
            return True
        try:
            win.show()
            win.restore()
        except Exception:
            pass
        try:  # поднять поверх остальных и сразу отпустить
            win.on_top = True
            win.on_top = False
        except Exception:
            pass
        return True

    def toggle_fullscreen(self):
        """F11: окно без рамки с заголовком и крестиком. Возвращает новое состояние."""
        win = self._window
        if not win:
            return False
        try:
            win.toggle_fullscreen()
            self._fullscreen = not self._fullscreen
        except Exception as exc:
            blog("PY   полноэкранный режим не вышел: " + exc_text(exc))
        return self._fullscreen

    def minimize_window(self):
        """В полноэкранном режиме системной кнопки нет — сворачиваем сами."""
        if self._window:
            try:
                self._window.minimize()
            except Exception as exc:
                blog("PY   свернуть не вышло: " + exc_text(exc))
        return True

    def hide_window(self):
        self._visible = False
        if self._window:
            try:
                self._window.hide()
            except Exception:
                pass
        return True

    def quit_app(self):
        self._quitting = True
        if self._tray:
            self._tray.stop()
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
        return True

    def _notify_done(self, chat, message):
        """Уведомление, когда ответ готов, а окно не на глазах."""
        if self._visible or not self._tray or message.get("error"):
            return
        title = (chat.get("title") or "Новый чат").strip()
        short = title[:12] + ("…" if len(title) > 12 else "")
        body = re.sub(r"\s+", " ", message.get("text") or "").strip()[:50]
        self._tray.notify('Gemini завершил работу в чате «%s»' % short, body)

    def pick_folder(self):
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                return result[0]
        except Exception:
            pass
        return ""

    def test_connection(self):
        try:
            models = make_backend(self.settings).models()
            return {"ok": True, "count": len(models)}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": http_error_text(exc)}
        except Exception as exc:
            return {"ok": False, "error": exc_text(exc)}

    def usage(self):
        """Расход по ключу. Остатка квоты не существует: Antigravity его не сообщает."""
        backend = make_backend(self.settings)
        if not hasattr(backend, "usage"):
            return {"ok": False, "error": "Этот провайдер расход не считает"}
        try:
            return {"ok": True, "usage": backend.usage()}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": http_error_text(exc)}
        except Exception as exc:
            return {"ok": False, "error": exc_text(exc)}

    def _mark_new(self, models):
        """Новой считается модель, которой не было в прошлый раз.

        При самом первом запуске новинок нет: иначе синие точки повисли бы
        на всём списке. Просто запоминаем, что видели.
        """
        seen = self.settings.get("seenModels")
        first_run = not isinstance(seen, list) or not seen
        known = set(seen or [])
        for m in models:
            m["isNew"] = bool(not first_run and m["id"] not in known)
        if first_run:
            self.settings["seenModels"] = sorted(m["id"] for m in models)
            write_json(SETTINGS_PATH, self.settings)
        return models

    def mark_models_seen(self):
        """Точки гаснут, когда список открыли."""
        ids = set(self.settings.get("seenModels") or [])
        ids.update(self._last_model_ids)
        self.settings["seenModels"] = sorted(ids)
        write_json(SETTINGS_PATH, self.settings)
        return {"ok": True}

    def list_models(self):
        blog("PY   list_models() начат")
        try:
            models = make_backend(self.settings).models()
            blog("PY   list_models() сеть отдала %d" % len(models))
            if not models:
                models = FALLBACK_MODELS
                return {"ok": True, "models": models, "groups": group_models(models),
                        "fallback": True}
            self._last_model_ids = [m["id"] for m in models]
            models = self._mark_new(models)
            return {"ok": True, "models": models, "groups": group_models(models),
                    "hasNew": any(m.get("isNew") for m in models)}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": http_error_text(exc),
                    "models": FALLBACK_MODELS, "groups": group_models(FALLBACK_MODELS)}
        except Exception as exc:
            return {"ok": False, "error": exc_text(exc),
                    "models": FALLBACK_MODELS, "groups": group_models(FALLBACK_MODELS)}

    # ---------- генерация

    def _clean_images(self, images):
        """Оставляем только картинки и только разумного размера."""
        out = []
        for item in (images or [])[:MAX_IMAGES]:
            uri = item.strip() if isinstance(item, str) else ""
            if not uri.startswith("data:image/"):
                continue
            if len(uri) > MAX_IMAGE_CHARS:
                blog("PY   картинка не принята, слишком большая: %d знаков" % len(uri))
                continue
            out.append(uri)
        return out

    def send(self, chat_id, text, images=None):
        chat = self._find(chat_id)
        if not chat:
            return {"ok": False, "error": "Чат не найден"}
        if chat_id in self.busy:
            return {"ok": False, "error": "Ответ ещё генерируется"}
        provider = self.settings.get("provider")
        key = self.settings.get("apiKey") if provider == "openai" else self.settings.get("googleKey")
        if not (key or "").strip():
            return {"ok": False, "error": "Укажите API-ключ в настройках"}
        text = (text or "").strip()
        images = self._clean_images(images)
        if not text and not images:
            return {"ok": False, "error": "Пустое сообщение"}

        self.cancelled.discard(chat_id)
        entry = {"role": "user", "text": text, "ts": now_ms()}
        if images:
            entry["images"] = images
        chat["messages"].append(entry)
        if chat.get("title") in (None, "", "Новый чат"):
            name = text or ("Картинка" if len(images) == 1 else "Картинки")
            chat["title"] = name[:48] + ("…" if len(name) > 48 else "")
            self.emit({"type": "title", "chatId": chat_id, "title": chat["title"]})
        chat["updatedAt"] = now_ms()
        self._persist_chat(chat)
        threading.Thread(target=self._generate, args=(chat_id,), daemon=True).start()
        return {"ok": True}

    def regenerate(self, chat_id):
        chat = self._find(chat_id)
        if not chat or chat_id in self.busy:
            return {"ok": False, "error": "Недоступно"}
        while chat["messages"] and chat["messages"][-1]["role"] == "model":
            chat["messages"].pop()
        if not chat["messages"]:
            return {"ok": False, "error": "Нечего перегенерировать"}
        self.cancelled.discard(chat_id)
        self._persist_chat(chat)
        self.emit({"type": "reload", "chatId": chat_id})
        threading.Thread(target=self._generate, args=(chat_id,), daemon=True).start()
        return {"ok": True}

    def _system_text(self):
        parts = []
        if (self.settings.get("globalPrompt") or "").strip():
            parts.append(self.settings["globalPrompt"].strip())
        if self.settings.get("toolsEnabled") and (self.settings.get("toolsPrompt") or "").strip():
            parts.append(self.settings["toolsPrompt"].strip())
        return "\n\n".join(parts)

    def _generate(self, chat_id):
        chat = self._find(chat_id)
        if not chat:
            return
        self.busy.add(chat_id)
        model = chat.get("model") or self.settings["model"]
        backend = make_backend(self.settings)
        message = {"role": "model", "text": "", "thoughts": "", "calls": [],
                   "ts": now_ms(), "model": model}
        chat["messages"].append(message)
        self.emit({"type": "start", "chatId": chat_id, "model": model})

        should_stop = lambda: chat_id in self.cancelled
        on_thought = lambda d: self.emit({"type": "thought", "chatId": chat_id, "delta": d})
        streamed = []   # что ушло в окно по кусочкам — сверяем с итоговым текстом

        def on_text(delta):
            streamed.append(delta)
            self.emit({"type": "delta", "chatId": chat_id, "delta": delta})

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                if should_stop():
                    break
                messages = backend.build_messages(chat, self._system_text())
                text, thoughts, calls, error = backend.stream(
                    model, messages, on_text, on_thought, should_stop)

                if error:
                    message["error"] = error
                    self.emit({"type": "error", "chatId": chat_id, "error": error})
                    break

                if not calls and self.settings.get("toolsEnabled"):
                    text, calls = extract_text_tool_calls(text)
                    if calls:
                        self.emit({"type": "replace_text", "chatId": chat_id, "text": text})

                if thoughts:
                    message["thoughts"] += thoughts
                if text:
                    message["text"] += ("\n\n" if message["text"] else "") + text
                # если итог разошёлся с тем, что печаталось на глазах
                # (шлюз прислал чистый текст вместо порванных дельт) — перерисовываем
                if message["text"] and message["text"] != "".join(streamed):
                    self.emit({"type": "replace_text", "chatId": chat_id,
                               "text": message["text"]})
                    streamed[:] = [message["text"]]
                if not calls or should_stop():
                    break

                for call in calls:
                    self.emit({"type": "tool_start", "chatId": chat_id,
                               "name": call["name"], "args": call["args"]})
                    impl = resolve_tool(call["name"])
                    if impl is None:
                        result = {"status": "error", "error": "Неизвестный инструмент"}
                    else:
                        try:
                            result = impl(call["args"], self.settings)
                        except Exception as exc:
                            result = {"status": "error", "error": exc_text(exc)}
                    record = {"id": call.get("id", "call_0"), "name": call["name"],
                              "args": call["args"], "result": result,
                              "native": bool(call.get("native"))}
                    message["calls"].append(record)
                    self.emit({"type": "tool_done", "chatId": chat_id, "call": record})
                self._persist_chat(chat)
            else:
                message["error"] = "Превышен лимит вызовов инструментов"
        except Exception:
            message["error"] = traceback.format_exc(limit=2)
            self.emit({"type": "error", "chatId": chat_id, "error": message["error"]})

        if should_stop():
            message["stopped"] = True
            self.cancelled.discard(chat_id)
        self.busy.discard(chat_id)
        chat["updatedAt"] = now_ms()
        self._persist_chat(chat)
        self.emit({"type": "done", "chatId": chat_id, "message": message})
        self._notify_done(chat, message)


def run_tool_process():
    """Второй экземпляр приложения, запущенный ради одного инструмента.

    Читает аргументы из stdin, печатает результат в stdout. Ничего от окна
    и настроек ему не нужно — поэтому и ошибка тут никому не навредит.
    """
    path = sys.argv[sys.argv.index("--run-tool") + 1]
    try:
        raw = sys.stdin.buffer.read()
        args = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except Exception:
        args = {}

    scope = {"__name__": "gemini_tool", "__file__": path}
    try:
        # utf-8-sig: файл могли открыть блокнотом, а он дописывает BOM
        with open(path, "r", encoding="utf-8-sig") as fh:
            exec(compile(fh.read(), path, "exec"), scope)
        run = scope.get("run")
        if not callable(run):
            raise RuntimeError("в файле нет функции run(args)")
        result = run(args)
    except Exception:
        out = {"status": "error", "error": traceback.format_exc(limit=3)[-900:]}
    else:
        out = result if isinstance(result, dict) else {"status": "ok", "result": result}

    # Пишем байтами: кодировка консоли на Windows не UTF-8, и кириллица,
    # пройдя через неё, возвращается в диалог мусором.
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False, default=str).encode("utf-8"))
    sys.stdout.buffer.flush()


def main():
    if "--run-tool" in sys.argv:
        run_tool_process()
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    # Если копия уже работает — отдаём ей управление и молча уходим. Журнал
    # запуска при этом не трогаем: он принадлежит той копии, и затирать его
    # значит остаться без следов, когда что-то пойдёт не так.
    if not claim_instance():
        wake_running(show="--tray" not in sys.argv[1:])
        return
    try:
        os.remove(BOOT_LOG)
    except Exception:
        pass
    blog("PY   main() старт")
    register_toast_name()
    api = Api()
    blog("PY   Api() готов")
    # --tray в автозапуске: поднимаемся молча, только значком в трее
    start_hidden = "--tray" in sys.argv[1:]
    api._visible = not start_hidden

    window = webview.create_window(
        APP_NAME,
        resource_path(os.path.join("ui", "index.html")),
        js_api=api,
        width=1320,
        height=880,
        min_size=(940, 620),
        background_color="#1b1c1d",
        text_select=True,
        hidden=start_hidden,
    )
    api._window = window
    serve_instance(api)

    # Крестик прячет окно в трей: генерация продолжается, и по готовности
    # прилетит уведомление. Полный выход — через меню значка.
    def on_closing():
        if api._quitting:
            return True
        # без значка в трее прятать окно некуда — закрываемся честно
        if not (api._tray and api._tray.icon):
            return True
        api.hide_window()
        return False

    window.events.closing += on_closing

    tray = Tray(api)
    api._tray = tray
    tray.start()

    blog("PY   окно создано, webview.start()")
    webview.start(debug=False)
    tray.stop()


if __name__ == "__main__":
    main()
