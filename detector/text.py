# -*- coding: utf-8 -*-
"""Разбор русского текста на абзацы, предложения и слова.

Готовых разборщиков не берём: нужен всего один язык и одна задача, зато
важно не спотыкаться о «А. В. Суворов» и «1725 г. в России» — иначе счёт
предложений поедет, а на нём держится половина признаков.
"""
import re

WORD = re.compile(r'[А-Яа-яЁёA-Za-z]+(?:[-‐‑][А-Яа-яЁёA-Za-z]+)*')

# Сокращения, после которых точка не кончает предложение.
ABBR = {
    'г', 'гг', 'в', 'вв', 'т', 'д', 'п', 'е', 'др', 'пр', 'им', 'ул', 'стр',
    'с', 'рис', 'табл', 'н', 'э', 'обл', 'руб', 'коп', 'тыс', 'млн', 'млрд',
    'проф', 'акад', 'ст', 'кн', 'гл', 'ч', 'см', 'изд', 'ред', 'сост', 'напр',
}

_END = re.compile(r'([.!?…]+)([»"\'\)\]]*)(\s+)')


def paragraphs(text):
    """Абзацы. Перенос внутри абзаца склеивается пробелом."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace(' ', ' ')
    if re.search(r'\n[ \t]*\n', text):
        parts = re.split(r'\n[ \t]*\n+', text)
        parts = [re.sub(r'\s*\n\s*', ' ', p) for p in parts]
    else:
        # Файл, где абзацы разделены одиночным переносом, тоже бывает.
        parts = text.split('\n')
    return [p.strip() for p in parts if p.strip()]


def sentences(para):
    """Предложения одного абзаца."""
    out = []
    start = 0
    for m in _END.finditer(para):
        nxt = para[m.end():m.end() + 1]
        # Строчная буква после точки — это не граница, а сокращение,
        # которого нет в списке, либо инициал.
        if nxt and not (nxt.isupper() or nxt.isdigit() or nxt in '«"—-('):
            continue
        if m.group(1) == '.':
            prev = re.search(r'([А-Яа-яЁёA-Za-z]+)\s*$', para[:m.start(1)])
            if prev:
                word = prev.group(1)
                if word.lower() in ABBR:
                    continue
                if len(word) == 1 and word.isupper():   # инициал: «А. В. Суворов»
                    continue
        chunk = para[start:m.end(2)].strip()
        if chunk:
            out.append(chunk)
        start = m.end()
    tail = para[start:].strip()
    if tail:
        out.append(tail)
    return out


def words(text):
    return WORD.findall(text)


# Маркер пункта списка. «o» здесь не опечатка: Word рисует им вложенный
# уровень, и в текст он попадает обычной латинской буквой.
LIST_MARK = re.compile(
    r'^\s*(?:[-–—•*o●▪]|\d+[.)]|[а-яА-Яa-zA-Z][.)])\s+')


def is_heading(block):
    """Заголовок ли это, а не предложение.

    Различать их приходится, потому что «2. Виды односоставных предложений»
    — не предложение: у него нет ни подлежащего, ни точки. Если считать
    такие строки наравне с прозой, поедет и ритм, и счёт имён собственных:
    заголовок почти весь состоит из слов с большой буквы.
    """
    w = words(block)
    if not w:
        return True
    if len(w) <= 3:
        return True
    return len(w) <= 10 and not re.search(r'[.!?…]["»\')\]]*$', block.strip())


class Doc:
    """Разобранный текст. Считается один раз, дальше признаки только читают."""

    def __init__(self, raw, name=''):
        self.name = name
        self.raw = raw.replace('\r\n', '\n').replace('\r', '\n')
        self.paras = paragraphs(raw)
        # У пунктов списка снимаем маркер, но содержимое оставляем: там
        # обычная проза. Заголовки выбрасываем целиком — предложениями
        # они не являются и мерить по ним ритм бессмысленно.
        self.marked = [bool(LIST_MARK.match(p)) for p in self.paras]
        self.blocks = [LIST_MARK.sub('', p).strip() for p in self.paras]
        self.heads = [is_heading(b) for b in self.blocks]
        self.prose = [b for b, h in zip(self.blocks, self.heads) if not h]

        self.sents = []
        for block in self.prose:
            self.sents.extend(sentences(block))
        self.flat = ' '.join(self.prose)
        self.low = self.flat.lower()
        self.words = words(self.flat)
        self.lower = [w.lower() for w in self.words]
        self.n_words = len(self.words)

        all_words = len(words(' '.join(self.blocks)))
        self.prose_share = self.n_words / float(all_words) if all_words else 0.0

    def per_k(self, count):
        """Сколько это на 1000 слов."""
        return 1000.0 * count / max(self.n_words, 1)

    def count_phrases(self, phrases):
        return sum(self.low.count(p) for p in phrases)
