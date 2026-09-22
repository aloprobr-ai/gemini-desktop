# -*- coding: utf-8 -*-
"""Счёт и калибровка.

Обучать что-то всерьёз на десяти текстах нельзя, поэтому обучения здесь нет.
Есть калибровка: по образцам считается, где у признака середина человеческих
значений, где машинных и насколько они вообще расходятся. Дальше новый текст
сравнивается с этими двумя облаками — это линейный дискриминант Фишера,
у которого каждое слагаемое читается отдельно и видно, что на что повлияло.

Вес признака — его же разделяющая сила: признак, который на ваших образцах
ничего не различает, сам собой перестаёт голосовать.
"""
import json
import math
import os
from statistics import mean, pstdev

from .features import FEATURES, extract

# Вклад одного признака ограничен: на десяти образцах один выброс
# иначе перевесит все остальные.
CAP = 3.0
DEFAULT_ALPHA = 1.6


class Model:
    def __init__(self, stats, alpha=DEFAULT_ALPHA, meta=None):
        self.stats = stats          # ключ -> {'h':.., 'a':.., 'sd':..}
        self.alpha = alpha
        self.meta = meta or {}

    # --------------------------------------------------------- заготовка
    @classmethod
    def prior(cls):
        """Модель без калибровки — на ожиданиях, зашитых в признаки."""
        stats = {f.key: {'h': f.human, 'a': f.ai, 'sd': f.sd} for f in FEATURES}
        return cls(stats, DEFAULT_ALPHA, {'source': 'заготовка'})

    @classmethod
    def load(cls, path):
        with open(path, 'r', encoding='utf-8') as fh:
            raw = json.load(fh)
        return cls(raw['stats'], raw.get('alpha', DEFAULT_ALPHA), raw.get('meta', {}))

    def save(self, path):
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'stats': self.stats, 'alpha': self.alpha, 'meta': self.meta},
                      fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------- счёт
    def score(self, feats):
        """Вероятность «это ИИ» и разбор по признакам."""
        parts = []
        for f in FEATURES:
            x = feats.get(f.key)
            st = self.stats.get(f.key)
            if x is None or st is None:
                continue
            sd = st['sd']
            if not sd or sd <= 0:
                continue
            pull = (st['a'] - st['h']) / sd          # куда и насколько сильно
            z = (x - (st['a'] + st['h']) / 2.0) / sd  # где мы относительно середины
            c = max(-CAP, min(CAP, pull * z))
            parts.append({'key': f.key, 'name': f.name, 'hint': f.hint,
                          'value': x, 'human': st['h'], 'ai': st['a'],
                          'contrib': c})
        if not parts:
            return 0.5, []
        logit = self.alpha * sum(p['contrib'] for p in parts) / len(parts)
        prob = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))
        prob = max(self.floor, min(1.0 - self.floor, prob))
        parts.sort(key=lambda p: -abs(p['contrib']))
        return prob, parts

    @property
    def floor(self):
        """Куда упирается уверенность.

        Десять образцов не дают права говорить «100%»: если классы разошлись
        начисто, это скорее про размер выборки, чем про силу признаков.
        Дальше 1/(n+2) не пускаем — столько знания в n примерах и есть.
        """
        n = self.meta.get('n_human', 0) + self.meta.get('n_ai', 0)
        return 1.0 / (n + 2.0) if n else 0.05


# --------------------------------------------------------------- калибровка

def _stats_from(human_rows, ai_rows):
    """Средние и общий разброс по каждому признаку."""
    stats = {}
    for f in FEATURES:
        hs = [r[f.key] for r in human_rows if r.get(f.key) is not None]
        as_ = [r[f.key] for r in ai_rows if r.get(f.key) is not None]
        if len(hs) < 2 or len(as_) < 2:
            # Не измерилось на большинстве образцов — оставляем ожидание.
            stats[f.key] = {'h': f.human, 'a': f.ai, 'sd': f.sd, 'fallback': True}
            continue
        mh, ma = mean(hs), mean(as_)
        # Общий разброс внутри групп: раздельные дисперсии на пяти образцах
        # оценивать бессмысленно.
        sd = math.sqrt((pstdev(hs) ** 2 + pstdev(as_) ** 2) / 2.0)
        floor = abs(f.sd) * 0.25 or 1e-6
        stats[f.key] = {'h': mh, 'a': ma, 'sd': max(sd, floor)}
    return stats


def cohen_d(stats, key):
    st = stats[key]
    return (st['a'] - st['h']) / st['sd'] if st['sd'] else 0.0


def fit(human_rows, ai_rows):
    """Модель по образцам плюс честная проверка «по одному отложенному»."""
    stats = _stats_from(human_rows, ai_rows)
    alpha, loo = _tune_alpha(human_rows, ai_rows)
    meta = {'source': 'калибровка', 'n_human': len(human_rows), 'n_ai': len(ai_rows)}
    model = Model(stats, alpha, meta)
    return model, loo


def _tune_alpha(human_rows, ai_rows):
    """Подбираем крутизну так, чтобы уверенность соответствовала попаданиям."""
    # Потолок у перебора низкий намеренно: на разделимых данных
    # правдоподобие растёт с крутизной без конца, и без границы
    # подбор ушёл бы в бесконечную самоуверенность.
    best, best_ll, best_loo = DEFAULT_ALPHA, None, None
    for step in range(1, 14):
        alpha = 0.4 * step
        loo = leave_one_out(human_rows, ai_rows, alpha)
        ll = 0.0
        for item in loo['items']:
            p = min(max(item['prob'], 1e-6), 1 - 1e-6)
            ll += math.log(p if item['truth'] == 'ии' else 1 - p)
        if best_ll is None or ll > best_ll:
            best, best_ll, best_loo = alpha, ll, loo
    return best, best_loo


def leave_one_out(human_rows, ai_rows, alpha):
    """Каждый образец проверяется моделью, построенной без него."""
    items = []
    # Та же граница уверенности, что и у готовой модели, иначе проверка
    # показывала бы проценты, которых потом никто не увидит.
    meta = {'n_human': len(human_rows), 'n_ai': len(ai_rows)}
    for truth, rows, others in (('человек', human_rows, ai_rows),
                                ('ии', ai_rows, human_rows)):
        for i, row in enumerate(rows):
            rest = rows[:i] + rows[i + 1:]
            if truth == 'человек':
                st = _stats_from(rest, others)
            else:
                st = _stats_from(others, rest)
            prob, _ = Model(st, alpha, meta).score(row)
            items.append({'name': row.get('__name__', '?'), 'truth': truth,
                          'prob': prob,
                          'right': (prob >= 0.5) == (truth == 'ии')})
    hits = sum(1 for it in items if it['right'])
    return {'items': items, 'hits': hits, 'total': len(items),
            'accuracy': hits / float(len(items)) if items else 0.0}


def model_path(root):
    return os.path.join(root, 'model.json')
