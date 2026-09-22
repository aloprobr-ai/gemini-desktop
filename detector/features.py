# -*- coding: utf-8 -*-
"""Признаки, по которым машинный текст отличается от человеческого.

Каждый признак — одно измеримое число и два ожидания: сколько его обычно
у человека и сколько у модели. Ожидания — это заготовка на случай, когда
калибровки ещё нет; после неё числа берутся из ваших же образцов.

Признак вправе вернуть None — «на таком тексте не измеряется». Тогда он
просто не участвует в подсчёте: соврать нулём было бы хуже.
"""
import re
from collections import Counter
from statistics import mean, pstdev

from .text import is_heading, words

FEATURES = []


class Feature:
    def __init__(self, key, name, hint, fn, human, ai, sd):
        self.key = key
        self.name = name
        self.hint = hint
        self.fn = fn
        self.human = human
        self.ai = ai
        self.sd = sd


def feature(key, name, hint, human, ai, sd):
    def deco(fn):
        FEATURES.append(Feature(key, name, hint, fn, human, ai, sd))
        return fn
    return deco


def extract(doc):
    """Все признаки одного текста: {ключ: число или None}."""
    return {f.key: f.fn(doc) for f in FEATURES}


# --------------------------------------------------------------- ритм

@feature('sent_cv', 'разнобой длины предложений',
         'у человека длина скачет, модель держит ровный размер',
         human=0.62, ai=0.38, sd=0.13)
def _sent_cv(doc):
    lens = [len(words(s)) for s in doc.sents]
    lens = [n for n in lens if n > 0]
    if len(lens) < 5:
        return None
    m = mean(lens)
    return pstdev(lens) / m if m else None


@feature('sent_mean', 'средняя длина предложения',
         'машинная проза тяготеет к длинным ровным периодам',
         human=11.0, ai=15.0, sd=4.0)
def _sent_mean(doc):
    lens = [len(words(s)) for s in doc.sents]
    lens = [n for n in lens if n > 0]
    return mean(lens) if len(lens) >= 3 else None


@feature('para_cv', 'разнобой длины абзацев',
         'модель нарезает абзацы одинаковыми ломтями',
         human=0.50, ai=0.30, sd=0.18)
def _para_cv(doc):
    lens = [len(words(p)) for p in doc.prose]
    lens = [n for n in lens if n > 0]
    if len(lens) < 4:
        return None
    m = mean(lens)
    return pstdev(lens) / m if m else None


# ------------------------------------------------------------- словарь

@feature('mattr', 'богатство словаря',
         'доля неповторяющихся слов в окне из 50 слов',
         human=0.87, ai=0.88, sd=0.04)
def _mattr(doc):
    w = doc.lower
    win = 50
    if len(w) < win + 20:
        return None
    vals = [len(set(w[i:i + win])) / float(win)
            for i in range(0, len(w) - win + 1, 5)]
    return mean(vals)


@feature('hapax', 'слова, сказанные один раз',
         'человек чаще роняет случайное слово и больше не возвращается',
         human=0.70, ai=0.66, sd=0.06)
def _hapax(doc):
    # Считаем в окне постоянного размера. Иначе признак меряет длину текста,
    # а не словарь: чем короче отрывок, тем меньше в нём поводов повториться.
    win = 200
    if doc.n_words < win + 50:
        return None
    c = Counter(doc.lower[:win])
    return sum(1 for v in c.values() if v == 1) / float(len(c))


# --------------------------------------------------------------- штампы

CLICHE = [
    'таким образом', 'кроме того', 'важно отметить', 'следует отметить',
    'стоит отметить', 'необходимо отметить', 'нельзя не отметить',
    'стоит подчеркнуть', 'важно понимать', 'важно помнить', 'в заключение',
    'подводя итог', 'в современном мире', 'в наши дни', 'сегодня как никогда',
    'играет важную роль', 'играет ключевую роль', 'ключевую роль',
    'имеет большое значение', 'трудно переоценить', 'сложно переоценить',
    'неотъемлемой частью', 'не менее важно', 'в первую очередь',
    'прежде всего', 'в свою очередь', 'с одной стороны', 'с другой стороны',
    'более того', 'в частности', 'благодаря этому', 'это позволяет',
    'что способствует', 'важнейшим аспектом', 'одним из главных',
    'одним из ключевых', 'залогом', 'на протяжении веков',
    'на протяжении всей истории', 'лежит в основе', 'формирует основу',
    'служит примером', 'яркий пример', 'наглядно демонстрирует',
]


# Длинные варианты идут в переборе первыми, и совпадения не накладываются:
# иначе «играет ключевую роль» засчиталось бы ещё раз как «ключевую роль».
CLICHE_RE = re.compile('|'.join(
    re.escape(p) for p in sorted(CLICHE, key=len, reverse=True)))


@feature('cliche', 'штампы-связки',
         '«таким образом», «важно отметить», «играет ключевую роль» и родня',
         human=6.0, ai=25.0, sd=10.0)
def _cliche(doc):
    return doc.per_k(len(CLICHE_RE.findall(doc.low)))


KANCEL_WORDS = re.compile(
    r'\b(являет(?:ся|ся)|являются|являлся|являлась|осуществля\w+|'
    r'данн(?:ый|ая|ое|ые|ого|ой|ым|ых)|характеризует\w*|обусловлен\w*|'
    r'обеспечивает\w*|способствует|посредством|представляет собой)\b')
KANCEL_ENDS = re.compile(r'\b[А-Яа-яЁё]{4,}(?:ание|ения|ение|аний|ений|ность|ности|ностью|ством)\b')


@feature('kancel', 'канцелярит',
         '«является», «осуществляется», «данный» и отглагольные существительные',
         human=25.0, ai=45.0, sd=15.0)
def _kancel(doc):
    n = len(KANCEL_WORDS.findall(doc.low)) + len(KANCEL_ENDS.findall(doc.low))
    return doc.per_k(n)


# ------------------------------------------------------------ конкретика

@feature('concrete', 'конкретика: цифры, даты, имена',
         'человек тащит из источника точные числа и фамилии, модель обобщает',
         human=60.0, ai=40.0, sd=18.0)
def _concrete(doc):
    if doc.n_words < 120:
        return None
    n = len(re.findall(r'\d+', doc.flat))
    for s in doc.sents:
        ws = words(s)
        # Первое слово пропускаем: оно с большой буквы просто потому,
        # что начинает предложение.
        n += sum(1 for w in ws[1:] if w[0].isupper())
    return doc.per_k(n)


# ------------------------------------------------------------ пунктуация

@feature('dash', 'тире',
         'в русском тире чаще всего заменяет связку, и люди ставят его охотно;'
         ' до калибровки признак почти молчит',
         human=10.0, ai=12.0, sd=7.0)
def _dash(doc):
    n = len(re.findall(r'[—–]', doc.flat))
    return doc.per_k(n)


@feature('livepunct', 'живая пунктуация',
         'восклицания, многоточия и настоящие риторические вопросы',
         human=5.0, ai=1.5, sd=3.5)
def _livepunct(doc):
    # Скобки убраны намеренно: в справочном тексте это пояснения
    # («(сделан из дерева)»), а не авторская реплика в сторону.
    n = len(re.findall(r'[!…]', doc.flat)) + len(re.findall(r'\.\.\.', doc.flat))
    # Вопросительный считаем, только если он закрывает настоящее
    # предложение. Иначе «какой? какая? какое? чей?» из разбора частей речи
    # выглядит как живой разговор с читателем — на этом счёт и сорвало.
    for s in doc.sents:
        if s.rstrip().endswith('?') and len(words(s)) >= 4 and not is_heading(s):
            n += 1
    return doc.per_k(n)


# ------------------------------------------------- следы живой руки

@feature('sloppy', 'следы живой руки',
         'двойные пробелы, пробел перед запятой, смесь кавычек, «--» вместо тире',
         human=1.5, ai=0.15, sd=0.9)
def _sloppy(doc):
    raw = doc.raw
    n = 0
    n += len(re.findall(r'\S {2,}\S', raw))
    n += len(re.findall(r'\s+[,;:!?]', raw))
    # Пропущенный пробел после знака. Два буквенных знака перед точкой —
    # чтобы не считать «т.д.» и инициалы.
    n += len(re.findall(r'(?<=[А-Яа-яЁёA-Za-z]{2})[,.;:!?](?=[А-Яа-яЁёA-Za-z])', raw))
    n += len(re.findall(r'(?<![-—])--(?!-)', raw))
    n += len(re.findall(r'(?<=[.!?]\s)[а-яё]', raw))
    if '«' in raw and '"' in raw:
        n += 3
    if '“' in raw or '”' in raw:
        n += 2
    return 1000.0 * n / max(len(raw), 1)


# -------------------------------------------------------------- фигуры

AND_INSIDE = re.compile(r'\S\s+и\s+\S')


@feature('tricolon', 'перечисления по три',
         'любимая фигура модели: «X, Y и Z»',
         human=5.0, ai=13.0, sd=5.0)
def _tricolon(doc):
    # Ищем не регуляркой по всему тексту — она расползается и ловит любое
    # «и» между двумя запятыми. Режем предложение по запятым и считаем
    # хвосты вида «..., Y и Z»: именно это и есть перечисление по три.
    if doc.n_words < 120:
        return None
    n = 0
    for s in doc.sents:
        parts = s.split(',')
        if len(parts) < 2:
            continue
        for part in parts[1:]:
            if AND_INSIDE.search(part):
                n += 1
    return doc.per_k(n)


CONNECTIVES = {
    'таким', 'кроме', 'однако', 'также', 'важно', 'следует', 'стоит',
    'например', 'помимо', 'более', 'вместе', 'наконец', 'во-первых',
    'во-вторых', 'в-третьих', 'итак', 'поэтому', 'благодаря', 'несмотря',
}


@feature('parallel', 'абзацы начинаются одинаково',
         'модель строит абзацы под копирку и открывает их связкой',
         human=0.12, ai=0.34, sd=0.13)
def _parallel(doc):
    if len(doc.prose) < 4:
        return None
    firsts = []
    for p in doc.prose:
        ws = words(p)
        if ws:
            firsts.append(ws[0].lower())
    if len(firsts) < 4:
        return None
    c = Counter(firsts)
    repeated = sum(v for v in c.values() if v > 1) / float(len(firsts))
    conn = sum(1 for f in firsts if f in CONNECTIVES) / float(len(firsts))
    return min(1.0, 0.5 * repeated + conn)


@feature('listy', 'списки',
         'модель раскладывает по пунктам даже там, где нужен связный рассказ',
         human=0.04, ai=0.22, sd=0.14)
def _listy(doc):
    if len(doc.paras) < 4:
        return None
    return sum(1 for m in doc.marked if m) / float(len(doc.paras))


@feature('headings', 'заголовки',
         'машинный конспект нарезан на озаглавленные разделы через каждый абзац',
         human=0.06, ai=0.20, sd=0.11)
def _headings(doc):
    if len(doc.paras) < 5:
        return None
    return sum(1 for h in doc.heads if h) / float(len(doc.paras))
