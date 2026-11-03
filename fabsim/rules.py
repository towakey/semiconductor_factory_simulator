"""ディスパッチルール (設計書 5.1)。
シグネチャ: rule(queue, equipment, now) -> Lot or None。
queue は QueueEntry のリスト。選ばれたエントリは queue から除去する。
"""


class QueueEntry(object):
    __slots__ = ("lot", "enqueue_time")

    def __init__(self, lot, t):
        self.lot = lot
        self.enqueue_time = t


def _remaining(lot):
    """残り工程の名目処理時間。"""
    prod = lot.product
    return sum(s.recipe.nominal_time(lot.wafers)
               for s in prod.route[lot.route_index:])


def _pick(queue, key):
    best_i, best_k = None, None
    for i, e in enumerate(queue):
        k = key(e)
        if best_k is None or k < best_k:
            best_k, best_i = k, i
    if best_i is None:
        return None
    return queue.pop(best_i)


def fifo(queue, equip, now):
    return _pick(queue, lambda e: (e.enqueue_time, e.lot.lot_id))


def priority(queue, equip, now):
    return _pick(queue, lambda e: (-e.lot.priority, e.enqueue_time, e.lot.lot_id))


def edd(queue, equip, now):
    return _pick(queue, lambda e: (e.lot.due_date, e.enqueue_time, e.lot.lot_id))


def spt(queue, equip, now):
    return _pick(queue, lambda e: (
        e.lot.next_step().recipe.nominal_time(e.lot.wafers),
        e.enqueue_time, e.lot.lot_id))


def min_setup(queue, equip, now):
    # 同一レシピ優先(段取り最小化)。同一レシピ内は FIFO。
    return _pick(queue, lambda e: (
        0 if e.lot.next_step().recipe.recipe == equip.last_recipe else 1,
        e.enqueue_time, e.lot.lot_id))


def cr(queue, equip, now):
    return _pick(queue, lambda e: (
        (e.lot.due_date - now) / max(_remaining(e.lot), 1e-9),
        e.enqueue_time, e.lot.lot_id))


RULES = {
    "FIFO": fifo,
    "PRIORITY": priority,
    "EDD": edd,
    "SPT": spt,
    "MIN_SETUP": min_setup,
    "CR": cr,
}
