"""
Centralized job filtering utilities
"""
import re
import unicodedata
from typing import List


def normalize_text(text: str) -> str:
    """Normalize text for consistent matching (handles unicode, case, whitespace)."""
    if not text:
        return ""
    # Normalize unicode characters (handles accents, special characters)
    normalized = unicodedata.normalize('NFD', str(text)).encode('ascii', 'ignore').decode('ascii')
    return normalized.lower().strip()


def should_filter_by_keywords(title: str, unwanted_keywords: List[str]) -> bool:
    """Centralized keyword filtering logic with word boundaries for specificity."""
    if not title or not unwanted_keywords:
        return False
    
    title_normalized = normalize_text(title)
    
    # Check each keyword with appropriate matching strategy
    for keyword in unwanted_keywords:
        if not keyword:
            continue
            
        # Keywords should already be normalized
        keyword_clean = keyword.strip()
        
        # For multi-word phrases, use substring matching
        if ' ' in keyword_clean:
            if keyword_clean in title_normalized:
                return True
        else:
            # For single words, use word boundary matching for specificity
            pattern = r'\b' + re.escape(keyword_clean) + r'\b'
            if re.search(pattern, title_normalized):
                return True
    
    return False


def should_filter_by_company(company: str, unwanted_companies: List[str]) -> bool:
    """Centralized company filtering logic."""
    if not company or not unwanted_companies:
        return False
        
    company_normalized = normalize_text(company)
    
    # Companies should already be normalized
    return any(unwanted_company in company_normalized for unwanted_company in unwanted_companies if unwanted_company)