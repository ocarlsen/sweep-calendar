"""Which side of a street line a curb is on."""
import math
from typing import List, Optional, Sequence

CARDINALS = ["East", "NorthEast", "North", "NorthWest", "West", "SouthWest", "South", "SouthEast"]


def normal_angle(line: Sequence[Sequence[float]], rightleft: str) -> Optional[float]:
    """Compass angle (degrees, east = 0, counterclockwise) of the normal to the
    line's overall direction, on its right ("R") or left ("L") as drawn."""
    if len(line) < 2:
        return None
    (x0, y0), (x1, y1) = line[0], line[-1]
    dx, dy = (x1 - x0) * math.cos(math.radians(y0)), y1 - y0
    nx, ny = (dy, -dx) if rightleft == "R" else (-dy, dx)
    if nx == 0 and ny == 0:
        return None
    return math.degrees(math.atan2(ny, nx))


def side_from_geometry(line: List[List[float]], rightleft: str) -> Optional[str]:
    """Cardinal side ('North', 'SouthEast', ...) of the R or L curb."""
    a = normal_angle(line, rightleft)
    return None if a is None else CARDINALS[round(a / 45) % 8]


def curb_for(line: Sequence[Sequence[float]], cardinal: str) -> str:
    """Inverse: which drawn side, "R" or "L", faces `cardinal`; "" if unknown."""
    a = normal_angle(line, "R")
    if a is None or cardinal not in CARDINALS:
        return ""
    want = CARDINALS.index(cardinal) * 45
    off = abs((a - want + 180) % 360 - 180)
    return "R" if off <= 90 else "L"
