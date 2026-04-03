"""
Data Processing Utilities
Helper functions for processing job data
"""

import pandas as pd
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs
import re
from datetime import datetime
from .filtering import should_filter_by_keywords, should_filter_by_company
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
    """Filter out jobs with unwanted keywords in the title"""
    if df.empty or 'title' not in df.columns:
        return df
    
    original_count = len(df)
    unwanted_keywords = ['internship', 'sales']
    
    for keyword in unwanted_keywords:
        mask = ~df['title'].str.contains(keyword, case=False, na=False)
        df = df.loc[mask]
    
    filtered_count = len(df)
    removed_count = original_count - filtered_count
    
    if removed_count > 0:
        print(f"Filtered out {removed_count} jobs containing unwanted keywords")
    
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


def export_jobs_to_formats(jobs_df: pd.DataFrame, base_filename: str) -> List[str]:
    """Export jobs data to multiple formats"""
    exported_files = []
    
    if jobs_df.empty:
        return exported_files
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Export to CSV
    csv_file = f"{base_filename}_{timestamp}.csv"
    jobs_df.to_csv(csv_file, index=False)
    exported_files.append(csv_file)
    
    # Export to Excel
    try:
        excel_file = f"{base_filename}_{timestamp}.xlsx"
        jobs_df.to_excel(excel_file, index=False)
        exported_files.append(excel_file)
    except ImportError:
        pass  # openpyxl not installed
    
    # Export to JSON
    json_file = f"{base_filename}_{timestamp}.json"
    jobs_df.to_json(json_file, orient='records', date_format='iso')
    exported_files.append(json_file)
    
    return exported_files


# -----------------------
# Job identity utilities
# -----------------------

def normalize_job_url(url: str, source: str) -> str:
    """Normalize a job URL to a stable identifier-like form.
    Removes tracking params; for known sources extracts canonical IDs.
    Returns empty string if url is falsy.
    """
    if not url:
        return ''
    try:
        parsed = urlparse(str(url))
        netloc = parsed.netloc.lower()
        path = parsed.path
        query = parse_qs(parsed.query)

        # LinkedIn: prefer /jobs/view/<id>
        if 'linkedin.' in netloc:
            # Extract numeric id from /jobs/view/<id>
            if '/jobs/view/' in path:
                parts = path.split('/jobs/view/')[-1].split('/')
                if parts and parts[0].isdigit():
                    return f"linkedin:{parts[0]}"
            # Fallback: try currentJobId param
            job_ids = query.get('currentJobId') or query.get('jobId') or []
            if job_ids:
                return f"linkedin:{job_ids[0]}"
            # Generic fallback: scheme://host/path without query/fragment
            return f"{netloc}{path}".rstrip('/')

        # Indeed: prefer viewjob `jk` parameter
        if 'indeed.' in netloc:
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
        return str(url).strip()


def build_job_ids(df: pd.DataFrame) -> pd.Series:
    """Build stable job_id Series for a DataFrame of jobs using normalized URLs when available.
    Falls back to normalized (title|company|source) if URL is missing.
    """
    if df.empty:
        return pd.Series(dtype='string')
    clean = df.copy()
    for col in ['url', 'title', 'company', 'location', 'source']:
        if col in clean.columns:
            clean[col] = clean[col].astype(str).str.strip()
        else:
            clean[col] = ''

    # Compute normalized URL ids
    norm_urls = clean.apply(lambda r: normalize_job_url(r.get('url', ''), r.get('source', '')), axis=1)
    title_norm = clean['title'].str.lower()
    company_norm = clean['company'].str.lower()
    source_norm = clean['source'].str.lower()

    fallback_ids = source_norm + '|' + title_norm + '|' + company_norm
    use_url_mask = norm_urls.str.len() > 0
    job_ids = norm_urls.where(use_url_mask, fallback_ids)
    return pd.Series(job_ids.values, index=df.index)


def build_normalized_keys(df: pd.DataFrame) -> pd.Series:
    """Build normalized deduplication keys for cross-run duplicate detection.
    Prefer normalized URL; otherwise use (source|title|company) ignoring location.
    """
    if df.empty:
        return pd.Series(dtype='string')
    clean = df.copy()
    for col in ['url', 'title', 'company', 'source']:
        if col in clean.columns:
            clean[col] = clean[col].astype(str).str.strip()
        else:
            clean[col] = ''
    norm_urls = clean.apply(lambda r: normalize_job_url(r.get('url', ''), r.get('source', '')), axis=1)
    title_norm = clean['title'].str.lower()
    company_norm = clean['company'].str.lower()
    source_norm = clean['source'].str.lower()
    fallback = source_norm + '|' + title_norm + '|' + company_norm
    use_url_mask = norm_urls.str.len() > 0
    keys = norm_urls.where(use_url_mask, fallback)
    return pd.Series(keys.values, index=df.index)
