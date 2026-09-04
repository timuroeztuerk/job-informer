"""
Data Processing Utilities
Helper functions for processing job data
"""

import pandas as pd
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse
import re
from .filtering import should_filter_by_keywords, should_filter_by_company, should_filter_study_title
from loguru import logger

def remove_historical_duplicates(self, current_df: pd.DataFrame) -> pd.DataFrame:
    """Remove jobs that already exist in database using fast SQLite lookup"""
    if current_df.empty:
        return current_df
    
    original_count = len(current_df)
    
    # Build normalized keys for current data (canonical URL or source|title|company)
    current_keys = build_normalized_keys(current_df)

    # Build normalized keys for existing DB rows
    with self.db._get_connection() as conn:
        existing_df = pd.read_sql_query(
            "SELECT url, title, company, source FROM jobs",
            conn
        )
    if existing_df.empty:
        filtered_df = current_df
    else:
        existing_keys = set(build_normalized_keys(existing_df).tolist())
        # Keep only rows whose normalized key is not present in DB
        mask = ~current_keys.isin(existing_keys)
        filtered_df = current_df.loc[mask]
    
    filtered_count = len(filtered_df)
    removed_count = original_count - filtered_count
    
    if removed_count > 0:
        logger.info(f"Removed {removed_count} jobs that already exist in database")
    else:
        logger.info("No database duplicates found - all jobs are new!")
    
    return filtered_df

def filter_unwanted_jobs(config, df: pd.DataFrame) -> pd.DataFrame:
    """Filter out jobs with unwanted keywords in the title and unwanted companies using centralized logic"""
    if df.empty:
        return df
        
    original_count = len(df)
    
    # Get unwanted keywords and companies from configuration
    unwanted_keywords = config.get_unwanted_keywords_list()
    unwanted_companies = []
    try:
        unwanted_companies = config.get_unwanted_companies_list()
    except Exception:
        unwanted_companies = []
        
    # Apply keyword filtering
    if unwanted_keywords:
        mask = ~df['title'].apply(lambda title: should_filter_by_keywords(title, unwanted_keywords))
        df = df.loc[mask]
        
    # Apply company filtering
    if unwanted_companies and 'company' in df.columns:
        mask = ~df['company'].apply(lambda company: should_filter_by_company(company, unwanted_companies))
        df = df.loc[mask]
    
    filtered_count = len(df)
    removed_count = original_count - filtered_count
    
    if removed_count > 0:
        logger.info(f"Filtered out {removed_count} jobs by unwanted filters (keywords/companies)")
        
    return df

def clean_job_data(jobs_df: pd.DataFrame) -> pd.DataFrame:
    """Clean and standardize job data"""
    if jobs_df.empty:
        return jobs_df
    
    # Make a copy to avoid modifying original
    df = jobs_df.copy()
    
    # Clean title
    if 'title' in df.columns:
        df['title'] = df['title'].str.strip()
        df['title'] = df['title'].str.replace(r'\s+', ' ', regex=True)
    
    # Clean company name
    if 'company' in df.columns:
        df['company'] = df['company'].str.strip()
        df['company'] = df['company'].str.replace(r'\s+', ' ', regex=True)
    
    # Clean location
    if 'location' in df.columns:
        df['location'] = df['location'].str.strip()
        df['location'] = df['location'].str.replace(r'\s+', ' ', regex=True)
    
    # Standardize salary information
    if 'salary' in df.columns:
        df['salary'] = df['salary'].fillna('Not specified')
        df['salary'] = df['salary'].str.strip()
    
    # Remove duplicate jobs (same title + company)
    if 'title' in df.columns and 'company' in df.columns:
        df = df.drop_duplicates(subset=['title', 'company'], keep='first')
    
    # Filter out unwanted job types
    df = filter_unwanted_job_titles(df)
    
    return df

def filter_unwanted_job_titles(df: pd.DataFrame) -> pd.DataFrame:
    """Filter out study-track roles such as internships or thesis positions."""
    if df.empty or 'title' not in df.columns:
        return df
    
    original_count = len(df)
    mask = ~df['title'].apply(should_filter_study_title)
    df = df.loc[mask]
    
    filtered_count = len(df)
    removed_count = original_count - filtered_count
    
    if removed_count > 0:
        print(f"Filtered out {removed_count} study-track jobs")
    
    return df

def extract_salary_info(salary_text: str) -> Dict[str, Optional[str]]:
    """Extract salary information from text"""
    if not salary_text or salary_text.lower() in ['not specified', 'n/a', '']:
        return {'min_salary': None, 'max_salary': None, 'currency': None, 'period': None}
    
    # Common patterns for salary extraction
    patterns = [
        r'(\$|€|£)(\d+,?\d*)k?\s*-\s*(\$|€|£)?(\d+,?\d*)k?',  # $50k - $70k
        r'(\$|€|£)(\d+,?\d*)k?',  # $50k
        r'(\d+,?\d*)k?\s*-\s*(\d+,?\d*)k',  # 50k - 70k
    ]
    
    result: Dict[str, Optional[str]] = {
        'min_salary': None,
        'max_salary': None,
        'currency': None,
        'period': None,
    }
    
    for pattern in patterns:
        match = re.search(pattern, salary_text, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                curr = groups[0] if groups[0] in ['$', '€', '£'] else '$'
                result['currency'] = str(curr)
                min_sal = groups[1]
                result['min_salary'] = min_sal.replace(',', '') if isinstance(min_sal, str) else None
                if len(groups) >= 4:
                    max_sal = groups[3]
                    result['max_salary'] = max_sal.replace(',', '') if isinstance(max_sal, str) else None
            break
    
    # Detect period (per hour, per year, etc.)
    if 'hour' in salary_text.lower():
        result['period'] = 'hourly'
    elif 'year' in salary_text.lower() or 'annual' in salary_text.lower():
        result['period'] = 'annual'
    elif 'month' in salary_text.lower():
        result['period'] = 'monthly'
    
    return result

def filter_jobs_by_keywords(jobs_df: pd.DataFrame, keywords: List[str], column: str = 'title') -> pd.DataFrame:
    """Filter jobs by keywords in specified column"""
    if jobs_df.empty or not keywords:
        return jobs_df
    
    pattern = '|'.join([re.escape(keyword) for keyword in keywords])
    mask = jobs_df[column].str.contains(pattern, case=False, na=False)
    return jobs_df.loc[mask]

def filter_jobs_by_location(jobs_df: pd.DataFrame, locations: List[str]) -> pd.DataFrame:
    """Filter jobs by location"""
    if jobs_df.empty or not locations:
        return jobs_df
    
    pattern = '|'.join([re.escape(location) for location in locations])
    mask = jobs_df['location'].str.contains(pattern, case=False, na=False)
    return jobs_df.loc[mask]

def generate_job_summary(jobs_df: pd.DataFrame) -> Dict:
    """Generate summary statistics for job data"""
    if jobs_df.empty:
        return {
            'total_jobs': 0,
            'sources': [],
            'top_companies': [],
            'top_locations': [],
            'keywords_found': []
        }
    
    summary = {
        'total_jobs': len(jobs_df),
        'sources': jobs_df['source'].unique().tolist() if 'source' in jobs_df.columns else [],
        'top_companies': jobs_df['company'].value_counts().head(5).to_dict() if 'company' in jobs_df.columns else {},
        'top_locations': jobs_df['location'].value_counts().head(5).to_dict() if 'location' in jobs_df.columns else {},
        'date_range': {
            'earliest': jobs_df['scraped_at'].min().isoformat() if 'scraped_at' in jobs_df.columns else None,
            'latest': jobs_df['scraped_at'].max().isoformat() if 'scraped_at' in jobs_df.columns else None
        }
    }
    
    return summary


# -----------------------
# Job identity utilities
# -----------------------

_MISSING_IDENTITY_VALUES = {"", "<na>", "nan", "nat", "none", "null"}


def _clean_identity_value(value: Any) -> str:
    """Return a trimmed scalar string, treating pandas-style missing values as empty."""
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        # Identity fields are expected to be scalar. If a non-scalar slips
        # through, converting it to text is still safer than raising here.
        pass
    clean = str(value).strip()
    return "" if clean.casefold() in _MISSING_IDENTITY_VALUES else clean


def _is_hostname(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def _parse_identity_url(value: str):
    """Parse absolute URLs and legacy scheme-less ``host/path`` values."""
    candidate = value
    if "://" not in candidate and re.match(
        r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]|$)",
        candidate,
        flags=re.IGNORECASE,
    ):
        candidate = f"//{candidate}"
    return urlparse(candidate)


def normalize_job_url(url: Any, source: Any = "") -> str:
    """Normalize a job URL to a stable identifier-like form.
    Removes tracking params; for known sources extracts canonical IDs.
    Returns empty string if url is falsy.
    """
    value = _clean_identity_value(url)
    if not value:
        return ''

    # Already-canonical keys should remain stable when they pass through the
    # database boundary again. Provider identifiers can contain dashes (for
    # example synthetic test data), so only the prefix is normalized here.
    canonical_match = re.fullmatch(r"(linkedin|indeed):([^/?#\s]+)", value, re.IGNORECASE)
    if canonical_match:
        return f"{canonical_match.group(1).lower()}:{canonical_match.group(2)}"

    try:
        parsed = _parse_identity_url(value)
        hostname = (parsed.hostname or '').lower()
        netloc = parsed.netloc.lower()
        path = unquote(parsed.path or '')
        query = parse_qs(parsed.query)

        # LinkedIn public links use both /jobs/view/<id> and
        # /jobs/view/<title-and-company-slug>-<id>. The numeric suffix is the
        # durable posting identity; the preceding slug can change.
        if _is_hostname(hostname, 'linkedin.com'):
            path_match = re.search(r"/jobs/view/([^/]+)", path, re.IGNORECASE)
            if path_match:
                slug = path_match.group(1).strip()
                id_match = re.fullmatch(r"(\d+)", slug) or re.search(r"-(\d{6,})$", slug)
                if id_match:
                    return f"linkedin:{id_match.group(1)}"

            # Fallback: try currentJobId param
            normalized_query = {key.casefold(): values for key, values in query.items()}
            job_ids = normalized_query.get('currentjobid') or normalized_query.get('jobid') or []
            for job_id in job_ids:
                clean_job_id = _clean_identity_value(job_id)
                if clean_job_id.isdigit():
                    return f"linkedin:{clean_job_id}"

            # Generic fallback: scheme://host/path without query/fragment
            return f"{netloc}{path}".rstrip('/')

        # Indeed: prefer viewjob `jk` parameter
        if _is_hostname(hostname, 'indeed.com'):
            job_ids = query.get('jk')
            if job_ids:
                return f"indeed:{job_ids[0]}"
            job_urls = parse_qs(parsed.fragment or '').get("jk")
            if job_urls:
                return f"indeed:{job_urls[0]}"
            return f"{netloc}{path}".rstrip('/')

        # Generic: host + path without query/fragment
        return f"{netloc}{path}".rstrip('/')
    except Exception:
        return value


def build_normalized_key(
    *,
    url: Any = "",
    source: Any = "",
    title: Any = "",
    company: Any = "",
) -> str:
    """Build the canonical scalar key used for job identity and deduplication."""
    normalized_url = normalize_job_url(url, source)
    if normalized_url:
        return normalized_url

    fallback_parts = [
        _clean_identity_value(source).casefold(),
        _clean_identity_value(title).casefold(),
        _clean_identity_value(company).casefold(),
    ]
    if not any(fallback_parts):
        return ""
    return "|".join(fallback_parts)


def build_job_ids(df: pd.DataFrame) -> pd.Series:
    """Build stable job_id Series for a DataFrame of jobs using normalized URLs when available.
    Falls back to normalized (title|company|source) if URL is missing.
    """
    if df.empty:
        return pd.Series(dtype='string')
    return build_normalized_keys(df)


def build_normalized_keys(df: pd.DataFrame) -> pd.Series:
    """Build normalized deduplication keys for cross-run duplicate detection.
    Prefer normalized URL; otherwise use (source|title|company) ignoring location.
    """
    if df.empty:
        return pd.Series(dtype='string')
    keys = df.apply(
        lambda row: build_normalized_key(
            url=row.get('url', ''),
            source=row.get('source', ''),
            title=row.get('title', ''),
            company=row.get('company', ''),
        ),
        axis=1,
    )
    return pd.Series(keys.values, index=df.index)
