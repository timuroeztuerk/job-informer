"""Small display normalization shared by market counts and job drill-downs."""

_CITY_ALIASES = {
    "munich": "München", "münchen": "München", "muenchen": "München",
    "zurich": "Zürich", "zürich": "Zürich", "zuerich": "Zürich",
    "cologne": "Köln", "köln": "Köln", "koeln": "Köln",
    "frankfurt": "Frankfurt am Main", "frankfurt am main": "Frankfurt am Main",
}


def primary_location(value: object) -> str:
    """Group known city spellings without inferring remote work or countries."""
    first = " ".join(str(value or "").split()).split(",", 1)[0].strip()
    return _CITY_ALIASES.get(first.casefold(), first or "Unknown")
