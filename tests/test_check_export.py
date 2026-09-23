# -*- coding: utf-8 -*-
"""/check, «переписать ещё раз по замечаниям» и выгрузка чатов.

Сеть не трогается: проверка и генерация подменены заглушками, выгрузка
пишет во временную папку.

    python tests/test_check_export.py
"""
import os
import shutil
import sys
import tempfile
import time

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


def make_api(chat):
    api = app.Api.__new__(app.Api)
    api.settings = dict(app.DEFAULT_SETTINGS)
    api.settings['apiKey'] = 'sk-test'
    api.events = []
    api.emit = lambda ev: api.events.append(ev)
    api._persist_chat = lambda c: None
    api.busy = set()
    api.cancelled = set()
    api.index = [{'id': chat['id'], 'title': chat.get('title')}]
    api._cache = {chat['id']: chat}
    api._find = lambda cid: chat if cid == chat['id'] else None
    api.checked = []
    api._run_check = lambda c, m, body: api.checked.append((m, body))
    api.generated = []
    api._generate = lambda cid: api.generated.append(cid)
    return api


def wait(cond, sec=2.0):
    end = time.time() + sec
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


TEXT = 'Первая учительница у нас была Анна Петровна. Худая, в очках, кричала редко.'
TAIL = '\n\nНужны детали: [нужна деталь]\n\nУбрал: канцелярит.'
CHECK = {
    'words': 80, 'short': False, 'disagree': False, 'judgeError': None,
    'features': {'prob': 0.7, 'verdict': 'скорее ИИ', 'top': [
        {'name': 'разнобой длины предложений', 'hint': 'модель держит ровный размер',
         'side': 'ии'},
        {'name': 'богатство словаря', 'hint': 'доля слов', 'side': 'человек'}]},
    'judge': {'prob': 0.8, 'spread': 0.1, 'runs': 3, 'failed': 0,
              'verdict': 'похоже на ИИ', 'summary': 'ровно и гладко',
              'observations': [
                  {'quote': 'кричала редко', 'means': 'штамп', 'points_to': 'ии'},
                  {'quote': 'Анна Петровна', 'means': 'имя', 'points_to': 'человек'}]},
}

print('/check разворачивается')
chat = {'id': 'c1', 'title': 'Новый чат', 'messages': [
    {'role': 'user', 'text': '/human ...', 'cmd': 'human', 'sendAs': 'промт'},
    {'role': 'model', 'text': TEXT + TAIL, 'ts': 1}]}
api = make_api(chat)
cmd = api._expand_command(chat, '/check')
check('команда узнана', cmd and cmd.get('kind') == 'check', cmd)
check('без текста берётся последний ответ, без отчёта редактора',
      cmd['target'] == TEXT, cmd.get('target'))
check('модель не зовётся', cmd.get('local') is True and 'send' not in cmd)
check('с текстом проверяется он',
      api._expand_command(chat, '/check другой текст')['target'] == 'другой текст')
check('пустой чат — внятная ошибка',
      'error' in api._expand_command({'id': 'x', 'messages': []}, '/check'))
check('в списке команд есть', '/check' in app.COMMANDS)

print('\n/check отправляется')
res = api.send('c1', '/check')
check('отправка принята', res == {'ok': True}, res)
check('генерация не запускалась', api.generated == [], api.generated)
check('проверка пошла', wait(lambda: len(api.checked) == 1), api.checked)
check('проверяется нужный текст', api.checked and api.checked[0][1] == TEXT)
asked, answer = chat['messages'][-2], chat['messages'][-1]
check('вопрос помечен как местный', asked.get('local') and asked.get('cmd') == 'check')
check('текст для повтора сохранён', asked.get('target') == TEXT)
check('развёрнутого промта нет', 'sendAs' not in asked)
check('ответ помечен как местный', answer.get('local') and answer.get('kind') == 'check')
check('окно получило «done»',
      any(ev['type'] == 'done' for ev in api.events), api.events)

hist = app.visible_history(chat)
check('модель /check не видит', all(not m.get('local') for m in hist) and len(hist) == 2)
check('/human после /check берёт настоящий ответ',
      api._last_model_text(chat) == (TEXT + TAIL).strip())

print('\nповтор /check')
api.checked[:] = []
res = api.regenerate('c1')
check('повтор принят', res == {'ok': True}, res)
check('повтор — снова проверка, а не генерация',
      wait(lambda: len(api.checked) == 1) and api.generated == [], api.generated)
check('старый итог заменён, а не добавлен',
      sum(1 for m in chat['messages'] if m.get('kind') == 'check') == 1)

print('\nзамечания для второго захода')
notes = app.rewrite_notes(CHECK)
check('цитата, выдавшая ИИ, передана', '«кричала редко» — штамп' in notes)
check('цитата в пользу человека не передана', 'Анна Петровна' not in notes)
check('признак, тянущий к ИИ, передан', 'модель держит ровный размер' in notes)
check('признак в пользу человека не передан', 'богатство словаря' not in notes)
check('процент назван', '80%' in notes)
check('без наблюдений — хотя бы сводка',
      'ровно и гладко' in app.rewrite_notes({'judge': {'prob': 0.9, 'summary': 'ровно и гладко',
                                                       'observations': []}}))
check('пустая проверка не роняет', 'ЗАМЕЧАНИЯ' in app.rewrite_notes({}))

print('\nпереписать ещё раз')
chat2 = {'id': 'c2', 'title': 't', 'messages': [
    {'role': 'user', 'text': '/human ...', 'cmd': 'human', 'sendAs': 'промт'},
    {'role': 'model', 'text': TEXT + TAIL, 'ts': 7, 'check': CHECK}]}
api2 = make_api(chat2)
res = api2.rewrite_again('c2', 7)
check('принято', res == {'ok': True}, res)
check('генерация пошла', wait(lambda: api2.generated == ['c2']), api2.generated)
last = chat2['messages'][-1]
check('это снова /human, и его снова проверят', last.get('cmd') == 'human')
check('в запросе переписанный текст без хвоста',
      TEXT in last['sendAs'] and 'Убрал:' not in last['sendAs'])
check('в запросе замечания', '«кричала редко»' in last['sendAs'])
check('в окне короткая строка, а не промт', last['text'].startswith('/human'))
check('чужая метка — отказ',
      api2.rewrite_again('c2', 999)['ok'] is False)

chat3 = {'id': 'c3', 'title': 't', 'messages': [
    {'role': 'user', 'text': '/check мой текст', 'cmd': 'check', 'local': True,
     'target': 'мой текст'},
    {'role': 'model', 'text': '', 'kind': 'check', 'local': True, 'ts': 5, 'check': CHECK}]}
api3 = make_api(chat3)
api3.rewrite_again('c3', 5)
check('после /check переписывается проверенный текст',
      'мой текст' in chat3['messages'][-1]['sendAs'])

busy = make_api(chat2)
busy.busy.add('c2')
check('пока идёт ответ — отказ', busy.rewrite_again('c2', 7)['ok'] is False)

print('\nвыгрузка в Markdown')
full = {'id': 'c4', 'title': 'Школа', 'createdAt': 1700000000000, 'model': 'gemini-x',
        'compactAt': 2, 'messages': [
            {'role': 'user', 'text': '/human про школу', 'sendAs': 'СЕКРЕТНЫЙ ПРОМТ',
             'images': ['data:image/png;base64,AAAA'], 'ts': 1700000000000},
            {'role': 'model', 'text': TEXT, 'model': 'gemini-x', 'check': CHECK,
             'calls': [{'name': 'create_file', 'result': {'path': 'C:\\a.txt'}}]},
            {'role': 'user', 'text': 'дальше'},
            {'role': 'model', 'text': '', 'error': 'шлюз молчит'}]}
md = app.chat_markdown(full)
check('заголовок', md.startswith('# Школа'))
check('видно то, что видел человек', '/human про школу' in md)
check('развёрнутый промт не уходит', 'СЕКРЕТНЫЙ ПРОМТ' not in md)
check('картинка не уходит мегабайтами', 'base64' not in md and 'Картинок приложено: 1' in md)
check('ответ на месте', TEXT in md)
check('инструмент отмечен', 'create_file' in md)
check('итог проверки на месте', '80% за ИИ' in md)
check('ошибка отмечена', 'Ошибка: шлюз молчит' in md)
check('свёртка отмечена', 'свёрнут' in md)
check('имя файла безопасное', app.export_name('a/b:c?') == 'b_c_.md', app.export_name('a/b:c?'))
check('пустое имя не пустое', app.export_name('') .endswith('.md'))

tmp = tempfile.mkdtemp()
try:
    api4 = make_api(full)
    api4.index = [{'id': 'c4'}, {'id': 'нет-такого'}]
    api4.pick_folder = lambda: tmp
    res = api4.export_all()
    check('выгрузка всех прошла', res.get('ok') and res.get('count') == 1, res)
    files = os.listdir(res['path']) if res.get('ok') else []
    check('по файлу на чат', files == ['Школа.md'], files)
    api4.pick_folder = lambda: ''
    check('отмена — не ошибка', api4.export_all() == {'ok': False, 'cancelled': True})

    empty = make_api({'id': 'e', 'title': 'пусто', 'messages': []})
    empty.pick_folder = lambda: tmp
    res = empty.export_all()
    check('пустые чаты не выгружаются', res.get('ok') is False and res.get('error'), res)
    check('пустая папка за собой убрана', len(os.listdir(tmp)) == 1, os.listdir(tmp))

    target = os.path.join(tmp, 'один')
    api4._save_dialog = lambda name: target
    res = api4.export_chat('c4')
    check('один чат сохранён, расширение дописано',
          res.get('ok') and res['path'] == target + '.md' and os.path.exists(target + '.md'), res)
    api4._save_dialog = lambda name: ''
    check('отмена сохранения — не ошибка', api4.export_chat('c4') == {'ok': False, 'cancelled': True})
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print('\nошибки шлюза по-человечески')


def http_err(code, body):
    import io as _io
    import urllib.error
    return urllib.error.HTTPError('https://x.invalid', code, 'err', {},
                                  _io.BytesIO(body.encode('utf-8')))


masked = app.http_error_text(http_err(
    502, '{"error":{"message":"Unknown endpoint: GET /v1/chat/completions"}}'))
check('подменённая nginx 502 объяснена',
      'не дождавшись' in masked and 'Unknown' not in masked, masked)
check('502 страницей html — тоже',
      'не дождавшись' in app.http_error_text(http_err(502, '<html>502</html>')))
honest = app.http_error_text(http_err(
    504, '{"error":{"message":"CLI did not answer within 180s."}}'))
check('честная ошибка шлюза не тронута',
      honest == 'HTTP 504: CLI did not answer within 180s.', honest)
check('прочая html-страница описана как раньше',
      'страницу ошибки' in app.http_error_text(http_err(500, '<html>500</html>')))

print('\nитого %d из %d' % (ok, ok + len(bad)))
if bad:
    print('не прошли: ' + ', '.join(bad))
sys.exit(1 if bad else 0)
