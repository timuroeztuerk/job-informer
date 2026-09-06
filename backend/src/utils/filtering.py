"""
Centralized job filtering utilities
"""
import re
import unicodedata
from typing import List, Optional

STUDY_ROLE_PATTERNS = [
    r"\bintern(?:ship)?\b",
    r"\bpraktik(?:ant|um)?\b",
    r"\b(?:schuler|schul|pflicht|sommer)praktik(?:ant\w*|um)\b",
    r"\bwerks?student\w*\b",
    r"\bwerkstudier\w*\b",
    r"\bworking student\b",
    r"\bstudent(?:ische| assistant| helper)?\b",
    r"\bstudentenjob\b",
    r"\bthesis\b",
    r"\bmaster(?:arbeit|thesis)\b",
    r"\bmasterand\w*\b",
    r"\bpre[\s-]*master\b",
    r"\bbachelor(?:arbeit|thesis)\b",
    r"\bbachelorand\w*\b",
    r"\bbachelorstudium\b",
    # Degree-course adverts, without treating a degree requirement or a
    # graduate's professional role as a student job.
    r"^bachelor(?![\s-]*absolvent)\s*[-:]\s*",
    r"^bachelor[\s/-]+of[\s/-]+(?:science|arts|engineering)\b",
    r"\bdissertation\b",
    r"\bdoctoral\b",
    r"\bphd(?: student)?\b",
    r"\btrainee(?:ship|s|programm?\w*)?\b",
    r"\bausbildung\b",
    r"\bduales?(?: studium)?\b",
    r"\bstudium\b",
    r"\babschlussarbeit\b",
    r"\bvolontariat\b",
    r"\b(?:graduate|it)[-/ ]programme?\b",
    r"\blehre\b",
    r"\bstudien[-/ ]?abschlussarbeit\b",
    r"\bhiwi\b",
]

ACADEMIC_ROLE_PATTERNS = [
    r"\bpost[\s-]?doc(?:toral)?\b",
    r"\bprofessor(?:in)?\b",
    r"\bjuniorprofessor(?:in)?\b",
    r"\b(?:junior)?professur\b",
    r"\blecturer\b",
    r"\bacademic researcher\b",
    r"\bscientific[\s/-]+employee\b",
    r"\bresearch fellow\b",
    r"\bdoktorand(?:in)?\b",
    r"\bwissenschaftlich\w*(?:[*/:_-]+[a-z]+|\([a-z]+\))?\s+mitarbeiter\w*\b",
    r"\bwiss\.?\s+(?:ma|mitarbeit\w*)\b",
    r"\bwissenschaftlich\w*\s+(?:position|postition)\w*\b",
    r"\bforschungsassistenz\w*\b",
]

SUBSTRING_KEYWORDS = {"entwickl", "informatik"}


def normalize_text(text: str) -> str:
    """Normalize text for consistent matching (handles unicode, case, whitespace)."""
    if not text:
        return ""
    # Normalize unicode characters (handles accents, special characters)
    # Preserve token boundaries before dropping non-ASCII characters. Otherwise
    # "SOC–Analyst" and non-breaking spaces become unmatchable concatenations.
    separated = re.sub(r"\s+", " ", str(text)).translate(
        str.maketrans({dash: "-" for dash in "‐‑‒–—−"})
    )
    normalized = unicodedata.normalize('NFD', separated).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r"\s+", " ", normalized.lower()).strip()


def match_keyword_filter(title: str, unwanted_keywords: List[str]) -> Optional[str]:
    """Return the matched keyword/phrase when a title should be filtered."""
    if not title or not unwanted_keywords:
        return None

    title_normalized = normalize_text(title)
    for keyword in unwanted_keywords:
        if not keyword:
            continue

        keyword_clean = keyword.strip()
        if not keyword_clean:
            continue

        if keyword_clean in SUBSTRING_KEYWORDS:
            if keyword_clean in title_normalized:
                return keyword_clean
        elif ' ' in keyword_clean:
            # Match complete phrase tokens, allowing common title separators. A
            # substring check would incorrectly treat "AI Engineering Manager"
            # as an "AI Engineer" role.
            tokens = [re.escape(token) for token in keyword_clean.split()]
            pattern = r'(?<!\w)' + r'[\s/-]+'.join(tokens) + r'(?!\w)'
            if re.search(pattern, title_normalized):
                return keyword_clean
        else:
            pattern = r'\b' + re.escape(keyword_clean) + r'\b'
            if re.search(pattern, title_normalized):
                return keyword_clean

    return None


def match_company_filter(company: str, unwanted_companies: List[str]) -> Optional[str]:
    """Return the matched company blacklist token when present."""
    if not company or not unwanted_companies:
        return None

    company_normalized = normalize_text(company)
    for unwanted_company in unwanted_companies:
        if not unwanted_company:
            continue
        if unwanted_company in company_normalized:
            return unwanted_company

    return None


def match_study_title_pattern(title: str) -> Optional[str]:
    """Return the matched study-role pattern when the title clearly describes one."""
    if not title:
        return None

    title_normalized = normalize_text(title)
    for pattern in STUDY_ROLE_PATTERNS:
        if re.search(pattern, title_normalized):
            return pattern
    return None


def should_filter_study_title(title: str) -> bool:
    """Return True when the title clearly describes internship/student/study-track roles."""
    return match_study_title_pattern(title) is not None


def match_academic_title_pattern(title: str, company: str = "") -> Optional[str]:
    """Return the matched pattern when a title clearly describes an academic role."""
    if not title:
        return None

    title_normalized = normalize_text(title)
    for pattern in ACADEMIC_ROLE_PATTERNS:
        if re.search(pattern, title_normalized):
            return pattern
    company_normalized = normalize_text(company)
    if re.search(r"\bresearch[\s/-]+(?:assistant|associate)\b", title_normalized) and re.search(
        r"\b(?:\w*universitat|\w*universitaet|university|hochschule|college|faculty|unsw|leibniz)\b",
        company_normalized,
    ):
        return "academic_company:research_assistant"
    return None


def should_filter_academic_title(title: str, company: str = "") -> bool:
    """Return True when the title clearly describes an academic role."""
    return match_academic_title_pattern(title, company) is not None
