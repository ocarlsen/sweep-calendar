"""Join block sides that a minor street splits in two.

Street centerline data breaks a block wherever any street touches it, so
Greenwich St from Franklin to Gough arrives as two segments split at Imperial
Alley. When the pieces are the same street, the same side, and the same
schedule, and the split is a minor street the city names, a resident thinks
of them as one block, so we present one.
"""
import math
from collections import defaultdict
from typing import Callable, Dict, List, Optional

from .model import BlockSide

TOUCH_M = 20  # segment ends this close count as connected


def ends_touch(a: BlockSide, b: BlockSide) -> bool:
    if not a.line or not b.line:
        return False
    for p in (a.line[0], a.line[-1]):
        for q in (b.line[0], b.line[-1]):
            k = math.cos(math.radians(p[1]))
            if math.hypot((p[0] - q[0]) * k, p[1] - q[1]) * 111_320 <= TOUCH_M:
                return True
    return False


def ends(side: BlockSide) -> List[str]:
    return [n.strip() for n in side.limits.split(" - ")]


def chain(group: List[BlockSide], minor: Callable[[str], bool]) -> List[List[BlockSide]]:
    """Split one (street, side, schedule) group into runs joined at minor streets."""
    links: Dict[int, List[int]] = defaultdict(list)
    for i, a in enumerate(group):
        for j in range(i + 1, len(group)):
            b = group[j]
            shared = set(ends(a)) & set(ends(b))
            if any(minor(n) for n in shared) and ends_touch(a, b):
                links[i].append(j)
                links[j].append(i)

    runs, seen = [], set()
    for start in range(len(group)):
        if start in seen or len(links[start]) > 1:
            continue  # begin walks only at run ends
        run, prev, cur = [], None, start
        while cur is not None and cur not in seen:
            seen.add(cur)
            run.append(group[cur])
            nxt = [n for n in links[cur] if n != prev]
            prev, cur = cur, (nxt[0] if len(nxt) == 1 else None)
        # Walk direction depends on input order; fix it so both sides of a
        # street read the same way ("Franklin St - Gough St").
        runs.append(run if run[0].id <= run[-1].id else run[::-1])
    # Anything unvisited sits in a loop or a branch; leave those pieces alone.
    runs += [[group[i]] for i in range(len(group)) if i not in seen]
    return runs


def joined_limits(run: List[BlockSide]) -> Optional[str]:
    first, last = ends(run[0]), ends(run[-1])
    if len(first) != 2 or len(last) != 2:
        return None
    inner_first = set(first) & set(ends(run[1]))
    inner_last = set(last) & set(ends(run[-2]))
    a = [n for n in first if n not in inner_first]
    b = [n for n in last if n not in inner_last]
    return f"{a[0]} - {b[0]}" if len(a) == 1 and len(b) == 1 else None


def join_split_blocks(sides: List[BlockSide], minor: Callable[[str], bool]) -> List[BlockSide]:
    groups: Dict[tuple, List[BlockSide]] = defaultdict(list)
    for s in sides:
        groups[(s.street, s.side, frozenset(s.rules))].append(s)

    out: List[BlockSide] = []
    for group in groups.values():
        for run in chain(group, minor):
            limits = joined_limits(run) if len(run) > 1 else None
            if limits is None:
                out.extend(run)
                continue
            head = run[0]
            head.limits = limits
            head.joined = run[1:]
            out.append(head)
    return out
