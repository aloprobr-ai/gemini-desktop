# -*- coding: utf-8 -*-
"""Отдельный прогон /compact: чат собирается готовым, запрос ровно один."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import app  # noqa: E402

NO_GLOBAL = '--no-global' in sys.argv

api = app.Api()
if NO_GLOBAL:
    api.settings['globalPrompt'] = ''
    api.settings['toolsEnabled'] = False
print('глобальный промт: %d знаков' % len(api.settings.get('globalPrompt') or ''))
print('инструменты: %s' % api.settings.get('toolsEnabled'))

chat = api.new_chat()
cid = chat['id']
chat['messages'] = [
    {'role': 'user', 'text': 'Чем отличается односоставное предложение от двусоставного?', 'ts': 0},
    {'role': 'model', 'text': 'В двусоставном есть и подлежащее, и сказуемое. '
                              'В односоставном главный член один, и второй не нужен: '
                              '«Вечереет», «Люблю грозу в начале мая».', 'ts': 0},
    {'role': 'user', 'text': 'А назывные это какие?', 'ts': 0},
    {'role': 'model', 'text': 'Те, где главный член — существительное в именительном: '
                              '«Зимний вечер. Тишина». Они просто утверждают, что нечто есть.', 'ts': 0},
]
api._persist_chat(chat)

try:
    print('\nотправляю /compact...')
    started = time.time()
    print('принято: %s' % api.send(cid, '/compact'))
    while cid in api.busy and time.time() - started < 400:
        time.sleep(0.5)
    took = round(time.time() - started, 1)

    chat = api._find(cid)
    msg = [m for m in chat['messages'] if m['role'] == 'model'][-1]
    print('ждали %s с' % took)
    if msg.get('error'):
        print('ОШИБКА: %s' % ' '.join(str(msg['error']).split())[:300])
    else:
        print('--- сводка (%d знаков) ---' % len(msg.get('text') or ''))
        for line in (msg.get('text') or '').strip().split('\n')[:22]:
            print('  ' + line)
    print('\ncompactAt = %s' % chat.get('compactAt'))
finally:
    api.delete_chat(cid)
    print('временный чат удалён')
