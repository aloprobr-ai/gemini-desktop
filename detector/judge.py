# -*- coding: utf-8 -*-
"""Второе мнение: спросить языковую модель.

Ходит в любую точку, совместимую с OpenAI (/v1/chat/completions) — в том
числе в свой шлюз agy на 127.0.0.1:8080/v1.

Модель не помнит, что она писала: памяти о собственной генерации у неё нет.
Она узнаёт стиль — то же самое, чем заняты признаки, только чутьё лучше.
Отсюда и слабость: аккуратный человеческий текст LLM охотно записывает в
машинные. Поэтому в запросе прямо сказано, что гладкость уликой не является,
и отдельно — про списанное из интернета: оно написано человеком, хотя и
звучит безлично.
"""
import json
import os
import re
import urllib.error
import urllib.request

SYSTEM = (
    'Ты определяешь, кем написан школьный доклад на русском языке: '
    'человеком или языковой моделью. Отвечай только JSON, без пояснений '
    'вокруг него.'
)

TASK = '''Перед тобой текст школьного доклада. Определи, писал его человек или языковая модель.

Порядок работы:
1. Сначала выпиши конкретные наблюдения. Каждое — короткая цитата из текста
   (не больше 12 слов) и что именно она показывает.
2. Только после этого назови вероятность.

Что НЕ является уликой против человека:
— Грамотность, гладкость, аккуратная структура. Школьники так пишут.
— Заголовки, списки и нумерация: так оформляют по требованию учителя.
— Безличный справочный тон. Текст, списанный из энциклопедии или с сайта,
  написан человеком, даже если звучит казённо. Не путай списывание с генерацией.

Что действительно указывает на модель:
— Ровный ритм: предложения и абзацы почти одной длины.
— Связки-штампы там, где они ничего не соединяют.
— Обобщения без единого конкретного факта, числа, имени или даты.
— Оговорки и уточнения, которых никто не просил.
— Безупречная согласованность частей при полном отсутствии авторского взгляда.

Что указывает на человека:
— Конкретика из источника: точные числа, фамилии, даты.
— Неровность: то длинно, то обрывисто.
— Ошибки в словах и в мысли: опечатки, сбитое согласование, потерянная нить,
  повтор уже сказанного, вывод, не следующий из сказанного.
— Личное суждение, оценка, обращение к читателю.

ОТДЕЛЬНО И ВАЖНО. Не путай следы НАПИСАНИЯ со следами ПЕРЕНОСА.
Текст мог быть скопирован из чата или из Word, и при копировании появляются:
— маркеры списка, ставшие буквами («o», «§», «·»), и сбитые отступы;
— обрыв на середине фразы, потерянный хвост;
— лишние или задвоенные знаки на стыках («чей?.»), двойные пробелы,
  разнобой кавычек, склеенные слова.
Всё это говорит только о том, что текст откуда-то копировали. Человек точно
так же копирует и машинный текст. Такие следы НЕ засчитывай в пользу человека
и вообще не выноси их в наблюдения. Смотри на язык и на мысль, а не на мусор
форматирования.

Верни строго такой JSON:
{
  "observations": [
    {"quote": "цитата", "means": "что показывает", "points_to": "ии" | "человек"}
  ],
  "probability_ai": число от 0 до 100,
  "summary": "один-два предложения"
}

Если уверенности нет, ставь probability_ai ближе к 50. Не округляй до 0 или 100:
таких оснований у тебя нет.

ТЕКСТ ДОКЛАДА:
---
%s
---'''


class JudgeError(RuntimeError):
    pass


def api_key(explicit=None):
    """Ключ. Наружу не показывается нигде: ни в выводе, ни в ошибках."""
    for value in (explicit, os.environ.get('AI_DETECT_KEY'),
                  os.environ.get('AGY_KEY'), os.environ.get('OPENAI_API_KEY')):
        if value:
            return value.strip()
    return ''


def ask(text, api, model, key=None, timeout=120):
    """Одно суждение модели. Возвращает разобранный ответ."""
    url = api.rstrip('/') + '/chat/completions'
    body = {
        'model': model,
        'temperature': 0,
        'messages': [
            {'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': TASK % text},
        ],
    }
    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'ai-detector/1.0')
    token = api_key(key)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:400]
        raise JudgeError('шлюз ответил %s: %s' % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise JudgeError('не достучаться до %s (%s)' % (url, exc.reason))
    except OSError as exc:
        raise JudgeError('не достучаться до %s (%s)' % (url, exc))

    try:
        content = payload['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise JudgeError('непонятный ответ: ' + json.dumps(payload)[:300])
    return _parse(content)


def _parse(content):
    """Достаёт JSON из ответа, даже если модель обернула его в ```json."""
    text = content.strip()
    fence = re.search(r'```(?:json)?\s*(.+?)```', text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find('{')
    if start < 0:
        raise JudgeError('в ответе нет JSON: ' + content[:200])
    # Именно raw_decode, а не поиск последней «}»: модель любит дописать
    # фразу после объекта, и кусок «от первой скобки до последней» тогда
    # захватывает этот хвост и перестаёт быть разбираемым.
    try:
        data, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError as exc:
        raise JudgeError('JSON не разобрался (%s): %s' % (exc, content[:200]))
    if not isinstance(data, dict):
        raise JudgeError('ожидался объект, пришло: ' + str(type(data).__name__))

    try:
        prob = float(data.get('probability_ai'))
    except (TypeError, ValueError):
        raise JudgeError('нет числа probability_ai в ответе модели')
    obs = data.get('observations')
    return {
        'prob': max(0.0, min(100.0, prob)) / 100.0,
        'summary': str(data.get('summary', '')).strip(),
        'observations': obs if isinstance(obs, list) else [],
    }


def ask_many(text, api, model, key=None, runs=1, timeout=120):
    """Несколько заходов подряд.

    Судья шумит: один и тот же текст при нулевой температуре всё равно
    получает разные оценки. Разброс между заходами показываем честно —
    если он широкий, верить среднему не стоит.
    """
    probs, results, errors = [], [], []
    for _ in range(max(1, runs)):
        try:
            res = ask(text, api, model, key, timeout)
        except JudgeError as exc:
            errors.append(str(exc))
            continue
        results.append(res)
        probs.append(res['prob'])
    if not probs:
        raise JudgeError(errors[0] if errors else 'модель не ответила')
    best = results[0]
    return {
        'prob': sum(probs) / len(probs),
        'spread': (max(probs) - min(probs)) if len(probs) > 1 else 0.0,
        'runs': len(probs),
        'failed': len(errors),
        'summary': best['summary'],
        'observations': best['observations'],
    }


# Публичное имя для разбора ответа. Нужно тем, кто ходит в сеть сам —
# например, через свой opener с прокси, — а разбирать хочет так же.
parse = _parse
