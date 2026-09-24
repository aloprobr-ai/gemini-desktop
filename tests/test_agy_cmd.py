# -*- coding: utf-8 -*-
"""/agy — промт agy на шлюзе, если шлюз разрешил это ключу.

Разрешение даёт хозяин шлюза на сервере (agy-prompt on all | on <ключ>).
Без него команды нет вовсе: «/agy ...» уходит модели обычным текстом.

Шлюз поддельный: крошечный HTTP-сервер в этом же процессе отвечает так же,
как /v1/agy-prompt настоящего, и запоминает, что ему прислали.

    python tests/test_agy_cmd.py
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

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


# --------------------------------------------------------- поддельный шлюз

KEY = 'sk-test-desktop-1234'
SEEN = []
STATE = {'allowed': False, 'off': False}


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def view(self):
        return {'object': 'agy_prompt', 'allowed': STATE['allowed'],
                'agy_prompt': 'off' if STATE['allowed'] and STATE['off'] else 'on'}

    def do_GET(self):
        SEEN.append(('GET', dict(self.headers), None))
        self.reply(200, self.view())

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        body = json.loads(raw.decode('utf-8'))
        SEEN.append(('POST', dict(self.headers), body))
        if not STATE['allowed']:
            return self.reply(403, {'error': {'message': 'Switching the agy prompt is not allowed for this key.'}})
        STATE['off'] = body.get('agy_prompt') == 'off'
        self.reply(200, self.view())


server = HTTPServer(('127.0.0.1', 0), Gateway)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d/v1' % server.server_port


def make_api(chat):
    api = app.Api.__new__(app.Api)
    api.settings = dict(app.DEFAULT_SETTINGS)
    api.settings.update({'apiKey': KEY, 'baseUrl': BASE, 'proxy': '', 'provider': 'openai'})
    api.events = []
    api.emit = lambda ev: api.events.append(ev)
    api._persist_chat = lambda c: None
    api.busy = set()
    api.cancelled = set()
    api._find = lambda cid: chat if cid == chat['id'] else None
    api.generated = []
    api._generate = lambda cid: api.generated.append(cid)
    api.checked = []
    api._run_check = lambda c, m, body: api.checked.append(body)
    return api


def wait(cond, sec=5.0):
    end = time.time() + sec
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def run(api, chat, text):
    """Отправить /agy и дождаться ответа шлюза в чате."""
    before = len(chat['messages'])
    res = api.send(chat['id'], text)
    if res != {'ok': True}:
        return res, None
    wait(lambda: len(chat['messages']) >= before + 2)
    return res, chat['messages'][-1]


app.remember_secrets({'apiKey': KEY})

# ------------------------------------------------------------- разбор

print('разбор /agy')
check('без слов — узнать', app.agy_args('') is None)
check('off и on', app.agy_args('off') == 'off' and app.agy_args('on') == 'on')
check('русские слова', app.agy_args('выкл') == 'off' and app.agy_args('вкл') == 'on')
check('чужих ключей и all нет', isinstance(app.agy_args('off all'), dict)
      and isinstance(app.agy_args('off stepa'), dict))
check('непонятное слово — ошибка с подсказкой',
      isinstance(app.agy_args('потом'), dict) and '/agy off' in app.agy_args('потом')['error'])

print('\nбез разрешения шлюза команды нет')
chat = {'id': 'a1', 'title': 'Новый чат', 'messages': [
    {'role': 'user', 'text': 'привет', 'ts': 1}, {'role': 'model', 'text': 'Привет!', 'ts': 2}]}
api = make_api(chat)
check('шлюз: не разрешено', api.agy_status() == {'allowed': False})
check('/agy — не команда', api._expand_command(chat, '/agy off') is None)
res = api.send('a1', '/agy off')
check('уходит модели обычным текстом', res == {'ok': True} and api.generated == ['a1'], api.generated)
check('POST на шлюз не уходил', all(s[0] == 'GET' for s in SEEN), SEEN)
chat['messages'] = chat['messages'][:2]

print('\nс разрешением')
STATE['allowed'] = True
api = make_api(chat)
check('шлюз: разрешено', api.agy_status() == {'allowed': True})
check('узнана и местная', (api._expand_command(chat, '/agy off') or {}).get('local') is True)
check('лишние слова — ошибка сразу', 'error' in api._expand_command(chat, '/agy off all'))

res, msg = run(api, chat, '/agy')
check('узнать: GET', res == {'ok': True} and SEEN[-1][0] == 'GET', res)
check('узнать: ответ местный', msg and msg.get('local') and msg.get('kind') == 'agy', msg)
check('узнать: в ответе состояние', msg and 'идёт как есть' in msg['text'], msg)
check('модель не звали', api.generated == [], api.generated)

res, msg = run(api, chat, '/agy выкл')
check('off: ушёл POST только со своим значением', SEEN[-1][0] == 'POST'
      and SEEN[-1][2] == {'agy_prompt': 'off'}, SEEN[-1][2])
check('off: ключ в заголовке, ничего лишнего', SEEN[-1][1].get('Authorization') == 'Bearer ' + KEY
      and 'X-Admin-Token' not in SEEN[-1][1])
check('off: ответ', msg and 'Готово' in msg['text'] and 'вырезается' in msg['text'], msg)

res, msg = run(api, chat, '/agy on')
check('on: промт вернулся', STATE['off'] is False and 'идёт как есть' in (msg or {}).get('text', ''), msg)
check('модель /agy не видит', all(not m.get('local') for m in app.visible_history(chat)))

before = len(SEEN)
check('повтор — снова запрос к шлюзу, не генерация',
      api.regenerate('a1') == {'ok': True} and wait(lambda: len(SEEN) > before) and api.generated == [])

print('\nразрешение отозвали, пока окно открыто')
STATE['allowed'] = False
res, msg = run(api, chat, '/agy off')
check('ошибка понятная', msg and 'больше не разрешает' in (msg.get('error') or ''), msg)
check('окно прячет команду', {'type': 'agy_allowed', 'allowed': False} in api.events, api.events[-3:])
check('дальше /agy — снова не команда', api._expand_command(chat, '/agy off') is None)
check('в ошибке нет ключа', msg and KEY not in (msg.get('error') or ''))

print('\nбез шлюза')
g = make_api(chat)
g.settings['provider'] = 'google'
check('Google API — не разрешено и не спрашиваем', g.agy_status() == {'allowed': False})
d = make_api(chat)
d.settings['baseUrl'] = 'http://127.0.0.1:9/v1'
check('шлюз не отвечает — не разрешено', d.agy_status() == {'allowed': False})

server.shutdown()
print('\nИтого: %d OK, %d FAIL' % (ok, len(bad)))
sys.exit(1 if bad else 0)
