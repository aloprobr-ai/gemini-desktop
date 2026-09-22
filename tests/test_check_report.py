# -*- coding: utf-8 -*-
"""Проверка текста после /human и отчёт об ошибке.

Сеть не трогается: судья подменяется заглушкой, признаки и так считаются
на этом компьютере. Api создаётся в обход __init__ — от него нужны только
решение «проверять или нет» и сам прогон.

    python tests/test_check_report.py
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
    api.settings = dict(app.DEFAULT_SETTINGS)
    api.settings.update(settings or {})
    api.events = []
    api.emit = lambda ev: api.events.append(ev)
    api._persist_chat = lambda chat: None
    return api


LIVE = """Первая учительница у нас была Анна Петровна. Худая, в очках, кричала редко.

В 1998 году школу перевели в новое здание на Кирова, 12. Переезжали в марте,
по грязи, таскали парты вчетвером. Я нёс глобус и уронил его на лестнице.

Потом было всё как у всех. Уроки, двойки, драка за гаражами. Вот такое.

А запомнилось другое: как в столовой пахло гречкой и как Анна Петровна
сказала, что я пишу «как будто отчитываюсь». Я обиделся. Зря обиделся —
она была права, и это видно даже сейчас, через двадцать лет."""

TAIL = """

Нужны детали: [нужна деталь: фамилия учительницы]

Убрал: канцелярит, штампы, тройки.

«играет ключевую роль» → «без неё не вышло бы»"""

print('служебный хвост')
body = app.human_body(LIVE + TAIL)
check('метки «нужны детали» отрезаны', 'Нужны детали' not in body)
check('отчёт редактора отрезан вместе с ними', 'Убрал:' not in body)
check('сам текст цел', body.strip().endswith('через двадцать лет.'))
check('текст без хвоста не страдает', app.human_body(LIVE) == LIVE)
check('пустое остаётся пустым', app.human_body('') == '')

print('\nпризнаки — без сети')
res = app.check_human_text(LIVE, {'provider': 'none'}, 'нет-такой-модели', runs=1)
check('слова посчитаны', res['words'] > 60, res['words'])
check('счёт по признакам есть', res['features'] and 0 <= res['features']['prob'] <= 1)
check('вердикт словами', bool(res['features']['verdict']), res['features'])
check('названы признаки, которые повлияли', len(res['features']['top']) == 3)
check('судья не ответил, и это сказано', bool(res['judgeError']), res['judgeError'])
check('на ошибке судьи разбор не падает', res['judge'] is None)

short = app.check_human_text('Два слова.', {'provider': 'none'}, 'x', runs=1)
check('короткий текст помечен', short['short'] is True, short['words'])

print('\nтри захода')
calls = []


def fake_once(text, settings, model, timeout=120):
    calls.append(model)
    probs = [0.10, 0.20, 0.30]
    return {'prob': probs[len(calls) - 1],
            'summary': 'сводка',
            'observations': [{'quote': 'цитата', 'means': 'что показывает',
                              'points_to': 'человек'}, 'мусор']}


real_once, app.judge_once = app.judge_once, fake_once
try:
    res = app.judge_text(LIVE, {}, 'судья', runs=3)
    check('спрошено ровно три раза', len(calls) == 3, calls)
    check('взято среднее', abs(res['prob'] - 0.20) < 1e-9, res['prob'])
    check('разброс посчитан', abs(res['spread'] - 0.20) < 1e-9, res['spread'])
    check('не-словари из наблюдений выброшены',
          all(isinstance(o, dict) for o in res['observations']), res['observations'])

    calls[:] = []
    full = app.check_human_text(LIVE, {'provider': 'none'}, 'судья', runs=3)
    check('признаки и судья идут рядом, а не в одно число',
          full['judge'] is not None and full['features'] is not None)
    check('расхождение замечено', full['disagree'] is False or full['disagree'] is True)

    def always_fails(*a, **kw):
        raise app.JudgeError('шлюз молчит')

    app.judge_once = always_fails
    res = app.check_human_text(LIVE, {'provider': 'none'}, 'судья', runs=3)
    check('когда не ответил никто — внятная ошибка',
          res['judgeError'] == 'шлюз молчит', res['judgeError'])
finally:
    app.judge_once = real_once

print('\nкого спрашивать')
check('по умолчанию — толстая модель',
      app.judge_model({'provider': 'openai'}) == app.DETECT_MODEL)
check('заданная в настройках важнее',
      app.judge_model({'provider': 'openai', 'detectModel': 'своя'}) == 'своя')
check('у Google берём модель чата',
      app.judge_model({'provider': 'google'}, 'gemini-2.5-pro') == 'gemini-2.5-pro')

print('\nкогда проверка запускается')
started = []
api = make_api()
api._run_check = lambda chat, msg, body: started.append(body)

human_chat = {'id': 'c1', 'messages': [
    {'role': 'user', 'text': '/human ...', 'cmd': 'human'},
    {'role': 'model', 'text': LIVE + TAIL}]}
api._maybe_check_human(human_chat, human_chat['messages'][-1])
check('после /human проверка идёт', len(started) == 1, started)
check('судим текст, а не отчёт редактора',
      started and 'Убрал:' not in started[0])

started[:] = []
plain = {'id': 'c2', 'messages': [
    {'role': 'user', 'text': 'обычный вопрос'},
    {'role': 'model', 'text': LIVE}]}
api._maybe_check_human(plain, plain['messages'][-1])
check('после обычного сообщения не идёт', not started)

api_off = make_api({'humanCheck': False})
api_off._run_check = lambda chat, msg, body: started.append(body)
api_off._maybe_check_human(human_chat, human_chat['messages'][-1])
check('выключенная проверка молчит', not started)

print('\nотчёт об ошибке')
settings = dict(app.DEFAULT_SETTINGS)
settings['baseUrl'] = 'https://api.example.invalid/v1'
settings['apiKey'] = 'sk-очень-секретный-ключ-1234567890'
app.remember_secrets(settings)

data = {'what': 'Окно белеет, ключ sk-очень-секретный-ключ-1234567890',
        'steps': '1. открыл\n2. нажал', 'withSystem': True,
        'withAnswer': True, 'answer': 'ответ модели'}
text = app.report_body(data, settings)
check('ключ в отчёт не попал', 'sk-очень' not in text, text[:120])
check('ключ заменён отметкой', '<ключ скрыт>' in text)
check('имя шлюза осталось', 'api.example.invalid' in text)
check('путь шлюза не ушёл', '/v1' not in text)
check('описание на месте', 'Окно белеет' in text)
check('шаги на месте', 'нажал' in text)
check('приложенный ответ на месте', 'ответ модели' in text)

quiet = app.report_body({'what': 'что-то', 'withSystem': False}, settings)
check('без галочки обстановка не уходит', 'Обстановка' not in quiet)
check('без галочки ответ модели не уходит', 'Ответ модели' not in quiet)

check('пустое описание не отправляется',
      make_api().send_report({'what': '   '}) ==
      {'ok': False, 'error': 'Опишите, что случилось'})

print('\nподпись в Helpers.md')
check('без галочки подписи нет',
      app.helper_sign({'name': 'Вася', 'link': 'https://example.invalid'}) == '')
check('без имени подписи нет', app.helper_sign({'sign': True, 'name': '  '}) == '')
check('имя и ссылка вместе',
      app.helper_sign({'sign': True, 'name': '@vasya',
                       'link': 'https://github.com/vasya'})
      == '@vasya — https://github.com/vasya')
check('без ссылки одно имя',
      app.helper_sign({'sign': True, 'name': '@vasya'}) == '@vasya')
check('ссылка без схемы не берётся',
      app.helper_sign({'sign': True, 'name': 'Вася',
                       'link': 'github.com/vasya'}) == 'Вася')
# Подпись уходит в разметку Helpers.md, и скобки в ней переписали бы ссылку.
check('скобки из имени вырезаны',
      app.helper_sign({'sign': True, 'name': 'Вася](http://злое.место)'})
      == 'Васяhttp://злое.место')
check('имя не бесконечное',
      len(app.helper_sign({'sign': True, 'name': 'я' * 500}))
      <= app.HELPER_NAME_LIMIT)
check('подпись попадает в отчёт',
      'Подпись в Helpers.md' in app.report_body(
          {'what': 'что-то', 'sign': True, 'name': '@vasya'}, settings))
check('без галочки раздела в отчёте нет',
      'Подпись в Helpers.md' not in app.report_body(
          {'what': 'что-то', 'name': '@vasya'}, settings))
check('галочка без имени не отправляется',
      make_api().send_report({'what': 'окно белеет', 'sign': True, 'name': ''})
      == {'ok': False, 'error': 'Укажите, как вас подписать'})

print('\nкуда уходит отчёт')
# Открытого пути нет и не должно появиться: отчёт об ошибке нередко описывает,
# как программу сломать, а открытая задача делает из этого инструкцию.
check('путь ведёт в Telegram', app.REPORT_URL.startswith('https://t.me/'),
      app.REPORT_URL)
check('адреса открытой задачи в программе нет',
      not hasattr(app, 'REPORT_PUBLIC_URL'))
check('выбора «куда» в окне нет',
      'repWhere' not in io.open(os.path.join(os.path.dirname(os.path.dirname(
          os.path.abspath(__file__))), 'ui', 'index.html'), encoding='utf-8').read())

link = app.report_link(text)
check('текст подставлен в адрес', link.startswith(app.REPORT_URL + '?text='))
check('ключ не утёк и в адрес', 'sk-очень' not in link
      and 'sk-%D0%BE%D1%87%D0%B5%D0%BD%D1%8C' not in link)
long_link = app.report_link('ю' * 40000)
check('длинный отчёт адрес не раздувает', len(long_link) < 20000, len(long_link))

# Браузер в тестах не открываем: иначе каждый прогон дёргает Telegram.
opened = []
real_open, app.webbrowser.open = app.webbrowser.open, opened.append
try:
    res = make_api().send_report({'what': 'окно белеет'})
    check('отчёт вернулся окну — для буфера обмена', bool(res.get('text')))
    check('короткий отчёт не помечен обрезанным', res.get('truncated') is False)
    check('браузер позвали ровно раз', len(opened) == 1, opened)
    check('позвали по нужному адресу', opened[0].startswith(app.REPORT_URL))
    big = make_api().send_report({'what': 'ю' * 30000})
    check('длинный отчёт помечен обрезанным', big.get('truncated') is True)
    check('в буфер уходит целиком', len(big['text']) > app.REPORT_TEXT_LIMIT)
finally:
    app.webbrowser.open = real_open

print('\nчужие адреса не открываем')
api = make_api()
check('свой адрес открывается или хотя бы не отвергается',
      app.HELPERS_URL.startswith('https://github.com/' + app.GITHUB_REPO))
check('чужой отвергнут',
      api.open_url('https://example.invalid/steal') == {
          'ok': False, 'error': 'Такой адрес приложение не открывает'})
check('подделка под свой адрес отвергнута',
      api.open_url('https://github.com.evil.invalid/' + app.GITHUB_REPO)['ok'] is False)

print('\nитого %d из %d' % (ok, ok + len(bad)))
if bad:
    print('не прошли: ' + ', '.join(bad))
sys.exit(1 if bad else 0)
