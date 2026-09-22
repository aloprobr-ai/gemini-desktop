# -*- coding: utf-8 -*-
"""Живой прогон команд через настоящий шлюз.

Окно не поднимается: Api без окна работает, emit просто молчит. Чат
заводится временный и в конце удаляется, чтобы не сорить в списке.

    .venv/Scripts/python.exe tests/live_commands.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import app  # noqa: E402

DEAD = '''Речевой этикет является неотъемлемой частью культуры общения. В современном мире важно отметить, что данный аспект играет ключевую роль в построении эффективной коммуникации. Таким образом, соблюдение правил речевого этикета способствует формированию благоприятной атмосферы, установлению доверия и достижению взаимопонимания между собеседниками.'''


def wait(api, chat_id, limit=300):
    start = time.time()
    while chat_id in api.busy and time.time() - start < limit:
        time.sleep(0.5)
    return round(time.time() - start, 1)


def last(api, chat_id):
    chat = api._find(chat_id)
    for m in reversed(chat['messages']):
        if m['role'] == 'model':
            return m
    return {}


def show_sent(api, chat_id, title):
    """Что на самом деле уходит модели — по ролям и первым словам."""
    chat = api._find(chat_id)
    backend = app.make_backend(api.settings)
    built = backend.build_messages(chat, api._system_text())
    rows = built['contents'] if isinstance(built, dict) else built
    print('    %s — уходит %d сообщений:' % (title, len(rows)))
    for r in rows:
        if isinstance(r, dict) and 'parts' in r:
            body = ' '.join(str(p.get('text', '')) for p in r['parts'])
            role = r.get('role')
        else:
            body = str(r.get('content', ''))
            role = r.get('role')
        body = ' '.join(body.split())
        print('      %-10s %s' % (role, (body[:90] + '…') if len(body) > 90 else body))


api = app.Api()
print('шлюз:  %s' % api.settings.get('baseUrl'))
print('модель: %s' % api.settings.get('model'))
print('глобальный промт: %d знаков' % len(api.settings.get('globalPrompt') or ''))
print()

chat = api.new_chat()
cid = chat['id']
print('временный чат %s' % cid)

try:
    # ---------------------------------------------------------- обычный ход
    print('\n1. Обычное сообщение')
    api.send(cid, 'Назови одним предложением, что такое речевой этикет.')
    print('   ждали %s с' % wait(api, cid))
    m = last(api, cid)
    print('   ответ: %s' % ' '.join((m.get('text') or m.get('error') or '(пусто)').split())[:140])

    # ---------------------------------------------------------------- /human
    print('\n2. /human с текстом')
    res = api.send(cid, '/human ' + DEAD)
    print('   принято: %s' % res)
    show_sent(api, cid, 'до ответа')
    print('   ждали %s с' % wait(api, cid))
    m = last(api, cid)
    out = m.get('text') or m.get('error') or '(пусто)'
    print('   --- что вернулось ---')
    for line in out.strip().split('\n')[:18]:
        print('   ' + line)
    chat = api._find(cid)
    user_msg = [x for x in chat['messages'] if x.get('cmd') == 'human'][-1]
    print('   в окне видно: %s' % user_msg['text'][:60])
    print('   модели ушло:  %d знаков промта' % len(user_msg['sendAs']))

    # -------------------------------------------------------------- /compact
    print('\n3. /compact')
    res = api.send(cid, '/compact')
    print('   принято: %s' % res)
    print('   ждали %s с' % wait(api, cid))
    m = last(api, cid)
    out = m.get('text') or m.get('error') or '(пусто)'
    print('   --- сводка ---')
    for line in out.strip().split('\n')[:20]:
        print('   ' + line)

    chat = api._find(cid)
    print('\n   compactAt = %s, всего сообщений %d'
          % (chat.get('compactAt'), len(chat['messages'])))
    show_sent(api, cid, 'после свёртки')

    # ------------------------------------------------------ ошибки по делу
    print('\n4. Отказы')
    empty = api.new_chat()
    print('   /compact в пустом чате: %s' % api.send(empty['id'], '/compact'))
    print('   /human в пустом чате:   %s' % api.send(empty['id'], '/human'))
    api.delete_chat(empty['id'])
finally:
    api.delete_chat(cid)
    print('\nвременный чат удалён')
