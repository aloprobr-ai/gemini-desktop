# -*- coding: utf-8 -*-
"""/agy — выключатель промта agy на шлюзе.

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

ADMIN = 'admin-token-xyz'
SEEN = []
STATE = {'all': 'on', 'keys': {}}
NAMES = ['desktop', 'Стёпа']


def view(admin):
    row = lambda n: {'name': n, 'agy_prompt': STATE['keys'].get(n, 'default'),
                     'effective': STATE['keys'].get(n, STATE['all'])}
    out = {'object': 'agy_prompt', 'interceptor': True, 'all': STATE['all'],
           'self': row('desktop'), 'admin': admin}
    if admin:
        out['keys'] = [row(n) for n in NAMES]
    return out


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

    def do_GET(self):
        SEEN.append(('GET', self.path, dict(self.headers), None))
        self.reply(200, view(self.headers.get('X-Admin-Token') == ADMIN))

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        body = json.loads(raw.decode('utf-8'))
        SEEN.append(('POST', self.path, dict(self.headers), body))
        admin = self.headers.get('X-Admin-Token') == ADMIN
        keys = body.get('keys')
        if keys and not admin:
            return self.reply(403, {'error': {'message': 'Changing other keys needs the admin token'}})
        if keys == 'all':
            STATE['all'] = body['agy_prompt']
        else:
            for n in (keys or ['desktop']):
                if n.lower() not in [x.lower() for x in NAMES]:
                    return self.reply(404, {'error': {'message': 'Unknown keys: ' + n}})
            for n in (keys or ['desktop']):
                name = next(x for x in NAMES if x.lower() == n.lower())
                if body['agy_prompt'] == 'default':
                    STATE['keys'].pop(name, None)
                else:
                    STATE['keys'][name] = body['agy_prompt']
        self.reply(200, view(admin))


server = HTTPServer(('127.0.0.1', 0), Gateway)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d/v1' % server.server_port


def make_api(chat, token=''):
    api = app.Api.__new__(app.Api)
    api.settings = dict(app.DEFAULT_SETTINGS)
    api.settings.update({'apiKey': 'sk-test-desktop', 'baseUrl': BASE, 'proxy': '',
                         'provider': 'openai', 'gatewayToken': token})
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
    """Отправить /agy и дождаться ответа в чате."""
    before = len(chat['messages'])
    res = api.send(chat['id'], text)
    if res != {'ok': True}:
        return res, None
    wait(lambda: len(chat['messages']) >= before + 2)
    return res, chat['messages'][-1]


# ------------------------------------------------------------- разбор

print('разбор /agy')
check('без слов — узнать', app.agy_args('') == (None, None))
check('off — свой ключ', app.agy_args('off') == ('off', None))
check('русские слова', app.agy_args('выкл') == ('off', None) and app.agy_args('вкл') == ('on', None))
check('all', app.agy_args('off all') == ('off', 'all'))
check('«все» — то же, что all', app.agy_args('on все') == ('on', 'all'))
check('имена через пробел и запятую',
      app.agy_args('off Стёпа, desktop') == ('off', ['Стёпа', 'desktop']))
check('default для ключа', app.agy_args('default Стёпа') == ('default', ['Стёпа']))
check('default для всех — ошибка', isinstance(app.agy_args('default all'), str))
check('all вперемешку с именами — ошибка', isinstance(app.agy_args('off all desktop'), str))
check('непонятное слово — ошибка с подсказкой',
      isinstance(app.agy_args('потом'), str) and '/agy off all' in app.agy_args('потом'))
check('в списке команд есть', '/agy' in app.COMMANDS)

print('\nкоманда в чате')
chat = {'id': 'a1', 'title': 'Новый чат', 'messages': [
    {'role': 'user', 'text': 'привет', 'ts': 1}, {'role': 'model', 'text': 'Привет!', 'ts': 2}]}
api = make_api(chat)
cmd = api._expand_command(chat, '/agy off')
check('узнана и местная', cmd.get('kind') == 'agy' and cmd.get('local') is True, cmd)
check('ошибка разбора — сразу, без сети',
      'error' in api._expand_command(chat, '/agy потом'))
g = make_api(chat)
g.settings['provider'] = 'google'
check('с Google API — внятный отказ', 'error' in g._expand_command(chat, '/agy'))

res, msg = run(api, chat, '/agy')
check('узнать: отправка принята', res == {'ok': True}, res)
check('узнать: GET без токена', SEEN and SEEN[-1][0] == 'GET'
      and 'X-Admin-Token' not in SEEN[-1][2], SEEN[-1:])
check('узнать: ответ местный', msg and msg.get('local') and msg.get('kind') == 'agy', msg)
check('узнать: в ответе состояние', msg and 'идёт как есть' in msg['text'], msg)
check('модель не звали', api.generated == [], api.generated)
check('окно получило done', any(e['type'] == 'done' for e in api.events))
check('модель /agy не видит', all(not m.get('local') for m in app.visible_history(chat)))

res, msg = run(api, chat, '/agy off')
check('свой ключ off: ушёл POST без keys',
      SEEN[-1][0] == 'POST' and SEEN[-1][3] == {'agy_prompt': 'off'}, SEEN[-1][3])
check('свой ключ off: ответ', msg and 'Готово' in msg['text'] and 'вырезается' in msg['text'], msg)
check('ключ ушёл в заголовке', SEEN[-1][2].get('Authorization') == 'Bearer sk-test-desktop')

res, msg = run(api, chat, '/agy off all')
check('all без токена — ошибка шлюза в окне',
      msg and msg.get('error') and '403' in msg['error'], msg)
check('в ошибке нет ключа', msg and 'sk-test-desktop' not in (msg.get('error') or ''))

print('\nс токеном управления')
app.remember_secrets({'apiKey': 'sk-test-desktop', 'gatewayToken': ADMIN})
api = make_api(chat, ADMIN)
res, msg = run(api, chat, '/agy off all')
check('all off принят', msg and not msg.get('error') and 'всех ключей' in msg['text'], msg)
check('токен ушёл в X-Admin-Token', SEEN[-1][2].get('X-Admin-Token') == ADMIN)
check('состояние шлюза: all off', STATE['all'] == 'off', STATE)
check('таблица ключей в ответе', msg and '| Стёпа |' in msg['text'], msg and msg['text'])

res, msg = run(api, chat, '/agy on стёпа')
check('по имени, без учёта регистра', STATE['keys'].get('Стёпа') == 'on', STATE)
check('токена нет в тексте ответа', msg and ADMIN not in msg['text'])

res, msg = run(api, chat, '/agy on вася')
check('неизвестный ключ — ошибка шлюза', msg and 'Unknown keys' in (msg.get('error') or ''), msg)

res, msg = run(api, chat, '/agy default Стёпа')
check('default снимает своё значение', 'Стёпа' not in STATE['keys'], STATE)

print('\nповтор и секреты')
before = len(SEEN)
res = api.regenerate('a1')
check('повтор принят', res == {'ok': True}, res)
check('повтор — снова запрос к шлюзу, не генерация',
      wait(lambda: len(SEEN) > before) and api.generated == [], api.generated)
pub = app.public_settings(api.settings)
check('токен наружу не отдаётся', pub.get('gatewayToken') == '' and pub.get('gatewayTokenSet') is True)
check('токен вычищается из текста', ADMIN not in app.scrub('ошибка ' + ADMIN))

server.shutdown()
print('\nИтого: %d OK, %d FAIL' % (ok, len(bad)))
sys.exit(1 if bad else 0)
