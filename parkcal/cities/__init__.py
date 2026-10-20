"""City registry. Adding a city = one module exposing CITY, listed here."""
from . import sf

CITIES = {c.slug: c for c in (sf.CITY,)}
