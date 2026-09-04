"""
Centralized job filtering utilities
"""
import re
import unicodedata
from typing import List, Optional

STUDY_ROLE_PATTERNS = [
    r"\bintern(?:ship)?\b",
    r"\bpraktik(?:ant|um)?\b",
    r"\bwerkstudent(?:in)?\b",
    r"\bworking student\b",
    r"\bstudent(?:ische| assistant| helper)?\b",
    r"\bstudentenjob\b",
    r"\bthesis\b",
    r"\bmaster(?:arbeit|thesis)\b",
    r"\bbachelor(?:arbeit|thesis)\b",
    r"\bdissertation\b",
    r"\bdoctoral\b",
    r"\bphd(?: student)?\b",
    r"\btrainee(?:ship)?\b",
    r"\bausbildung\b",
    r"\bduales?(?: studium)?\b",
    r"\bstudien[-/ ]?abschlussarbeit\b",
    r"\bresearch assistant\b",
    r"\bhiwi\b",
]

ACADEMIC_ROLE_PATTERNS = [
    r"\bpost[\s-]?doc(?:toral)?\b",
    r"\bprofessor(?:in)?\b",
    r"\bjuniorprofessor(?:in)?\b",
    r"\blecturer\b",
    r"\bacademic researcher\b",
    r"\bresearch fellow\b",
    r"\bdoktorand(?:in)?\b",
    r"\bwissenschaftlich\w*\*?n?\s+mitarbeiter\w*\b",
]

SUBSTRING_KEYWORDS = {"entwickl", "informatik"}


def normalize_text(text: str) -> str:
    """Normalize text for consistent matching (handles unicode, case, whitespace)."""
    if not text:
        return ""
    # Normalize unicode characters (handles accents, special characters)
    normalized = unicodedata.normalize('NFD', str(text)).encode('ascii', 'ignore').decode('ascii')
    return normalized.lower().strip()


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
            if keyword_clean in title_normalized:
                return keyword_clean
        else:
            pattern = r'\b' + re.escape(keyword_clean) + r'\b'
            if re.search(pattern, title_normalized):
                return keyword_clean

    return None


def should_filter_by_keywords(title: str, unwanted_keywords: List[str]) -> bool:
    """Centralized keyword filtering logic with word boundaries for specificity."""
    return match_keyword_filter(title, unwanted_keywords) is not None


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


def should_filter_by_company(company: str, unwanted_companies: List[str]) -> bool:
    """Centralized company filtering logic."""
    return match_company_filter(company, unwanted_companies) is not None


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


def match_academic_title_pattern(title: str) -> Optional[str]:
    """Return the matched pattern when a title clearly describes an academic role."""
    if not title:
        return None

    title_normalized = normalize_text(title)
    for pattern in ACADEMIC_ROLE_PATTERNS:
        if re.search(pattern, title_normalized):
            return pattern
    return None


def should_filter_academic_title(title: str) -> bool:
    """Return True when the title clearly describes an academic role."""
    return match_academic_title_pattern(title) is not None
