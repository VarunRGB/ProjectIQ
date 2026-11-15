
from common import main_menu
import heapq


class TaskQueue:
    def __init__(self):
        self._heap = []
        self._count = 0

    def push(self, priority, task):
        heapq.heappush(self._heap, (-priority, self._count, task))
        self._count += 1

    def pop(self):
        if not self._heap:
            return None
        return heapq.heappop(self._heap)[2]

    def peek_all(self):
        return [t for _, _, t in sorted(self._heap)]


def schedule(tasks, hours):
    q = TaskQueue()
    for t in tasks:
        q.push(t["priority"], t)
    plan, used = [], 0
    while True:
        t = q.pop()
        if t is None or used + t["hours"] > hours:
            break
        plan.append(t["name"])
        used += t["hours"]
    return plan
