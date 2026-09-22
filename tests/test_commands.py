# -*- coding: utf-8 -*-
"""Проверка команд /human и /compact.

Окно не поднимается: Api создаётся в обход __init__, от него нужны только
разбор команды и свёртка. Бэкенды собираются так же — им для сборки
истории настройки не нужны.

    python tests/test_commands.py
"""
import io
import os
import sys

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


def make_api(settings=None):
    api = app.Api.__new__(app.Api)
    api.settings = settings if settings is not None else {}
    api.events = []
    api.emit = lambda ev: api.events.append(ev)
    return api


def chat_with(*pairs):
    msgs = []
    for role, text in pairs:
        msgs.append({'role': role, 'text': text, 'ts': 0})
    return {'id': 'test1', 'title': 'Новый чат', 'messages': msgs}


api = make_api()

print('/human')
c = chat_with(('user', 'привет'), ('model', 'Ответ модели про офицеров.'))
cmd = api._expand_command(c, '/human Текст который надо оживить')
check('текст из команды попадает в запрос',
      cmd and 'Текст который надо оживить' in cmd['send'])
check('запрос — это HUMAN_PROMPT', cmd and 'ВЫРЕЗАТЬ БЕЗ ЖАЛОСТИ' in cmd['send'])
check('метки подставлены полностью',
      cmd and '{{ТЕКСТ}}' not in cmd['send'] and '{{ОБРАЗЕЦ}}' not in cmd['send'])
# В промте есть «10–20%» и «47%». Пока подстановка шла через %s, такой
# промт уронил бы отправку — поэтому проценты проверяем отдельно.
check('проценты в промте уцелели', cmd and '10–20%' in cmd['send'])
check('без образца стиля так и сказано',
      cmd and 'не приложен' in cmd['send'])
check('образец стиля из настроек доезжает',
      'МОЙ ОБРАЗЕЦ ТЕКСТА' in make_api({'styleSample': 'МОЙ ОБРАЗЕЦ ТЕКСТА'})
      ._expand_command(c, '/human что-то')['send'])
check('вид команды', cmd and cmd['kind'] == 'human', cmd)
check('заголовок чата берётся из текста, а не из «/human»',
      cmd and cmd['title'].startswith('Текст который'))

cmd = api._expand_command(c, '/human')
check('без аргумента берётся последний ответ модели',
      cmd and 'Ответ модели про офицеров.' in cmd['send'])

cmd = api._expand_command(chat_with(), '/human')
check('в пустом чате — внятная ошибка', cmd and 'error' in cmd, cmd)

check('регистр не важен',
      (api._expand_command(c, '/HUMAN привет') or {}).get('kind') == 'human')
check('чужая косая чертой не команда',
      api._expand_command(c, '/usr/bin/php это путь') is None)
check('обычный текст не трогаем', api._expand_command(c, 'просто текст') is None)

print('/compact')
check('сворачивать пустой чат нечего',
      'error' in (api._expand_command(chat_with(('user', 'привет')), '/compact') or {}))
cmd = api._expand_command(c, '/compact')
check('запрос — это COMPACT_PROMPT', cmd and 'Что сделано' in cmd['send'])
cmd2 = api._expand_command(c, '/compact особенно про шлюз')
check('уточнение после команды доезжает',
      cmd2 and 'особенно про шлюз' in cmd2['send'])

print('свёртка')
c = chat_with(('user', 'первый вопрос'), ('model', 'первый ответ'),
              ('user', 'второй вопрос'), ('model', 'второй ответ'))
c['messages'].append({'role': 'user', 'text': '/compact', 'ts': 0,
                      'sendAs': app.COMPACT_PROMPT, 'cmd': 'compact'})
c['messages'].append({'role': 'model', 'text': 'СВОДКА: делали то-то.', 'ts': 0})
api.events = []
api._apply_compaction(c)
check('compactAt указывает на команду', c.get('compactAt') == 4, c.get('compactAt'))
check('приказ «сверни» подменён', c['messages'][4]['sendAs'] == app.COMPACT_RESUME)
check('о свёртке сообщено в окно',
      any(e['type'] == 'compacted' for e in api.events))

hist = app.visible_history(c)
check('модели видно ровно два сообщения', len(hist) == 2, len(hist))
check('первое из них — пользовательское', hist[0]['role'] == 'user')
check('сводка на месте', 'СВОДКА' in hist[1]['text'])
check('история на диске цела', len(c['messages']) == 6)

api._apply_compaction(c)
check('повторная свёртка ничего не портит', c['compactAt'] == 4)

print('сборка истории для моделей')
ob = app.OpenAIBackend.__new__(app.OpenAIBackend)
msgs = ob.build_messages(c, 'системный текст')
check('система первой', msgs[0]['role'] == 'system')
check('свёрнутое не уходит',
      not any('первый вопрос' in str(m.get('content', '')) for m in msgs))
check('рамка вместо приказа', any(app.COMPACT_RESUME == m.get('content') for m in msgs))

gb = app.GoogleBackend.__new__(app.GoogleBackend)
built = gb.build_messages(c, 'системный текст')
parts = built['contents']
check('Google: первым идёт user', parts[0]['role'] == 'user',
      parts[0]['role'] if parts else 'пусто')
check('Google: свёрнутое не уходит',
      not any('первый вопрос' in str(p) for p in parts))

print('подмена текста для модели')
c2 = chat_with(('user', 'привет'))
c2['messages'][0]['sendAs'] = 'РАЗВЁРНУТЫЙ ЗАПРОС'
m2 = app.OpenAIBackend.__new__(app.OpenAIBackend).build_messages(c2, '')
check('модели уходит sendAs', m2[0]['content'] == 'РАЗВЁРНУТЫЙ ЗАПРОС')
check('в окне остаётся то, что напечатал человек',
      c2['messages'][0]['text'] == 'привет')

print('картинка в сводке')
c3 = chat_with(('user', 'вопрос'), ('model', 'ответ'))
c3['messages'].append({'role': 'user', 'text': '/compact', 'ts': 0,
                       'sendAs': app.COMPACT_PROMPT, 'cmd': 'compact'})
c3['messages'].append({'role': 'model', 'ts': 0, 'text':
    '1. О чём разговор\nРазбор предложений.\n\n'
    '![winter_evening.jpg](https://example.invalid/files/abc.jpg)'})
api2 = make_api()
api2._apply_compaction(c3)
summary = c3['messages'][-1]['text']
check('ссылка на картинку выброшена', '![' not in summary, summary[-60:])
check('текст сводки уцелел', 'Разбор предложений.' in summary)
check('окно получило исправленный текст',
      any(e['type'] == 'replace_text' for e in api2.events))
check('свёртка всё равно состоялась', c3.get('compactAt') == 2)

c4 = chat_with(('user', 'вопрос'), ('model', 'ответ'))
c4['messages'].append({'role': 'user', 'text': '/compact', 'ts': 0,
                       'sendAs': app.COMPACT_PROMPT, 'cmd': 'compact'})
c4['messages'].append({'role': 'model', 'ts': 0,
                       'text': '![only.png](http://x/y.png)'})
api3 = make_api()
api3._apply_compaction(c4)
check('сводка из одной картинки свёрткой не считается', c4.get('compactAt') is None)

print('\nитого %d из %d' % (ok, ok + len(bad)))
if bad:
    print('не прошли: ' + ', '.join(bad))
sys.exit(1 if bad else 0)
