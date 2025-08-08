"""
Data Processing Utilities
Helper functions for processing job data
"""

import pandas as pd
from typing import Dict, List, Optional
import re
from datetime import datetime


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
        df = df[mask]
    
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
    
    result = {'min_salary': None, 'max_salary': None, 'currency': None, 'period': None}
    
    for pattern in patterns:
        match = re.search(pattern, salary_text, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                result['currency'] = groups[0] if groups[0] in ['$', '€', '£'] else '$'
                result['min_salary'] = groups[1].replace(',', '')
                if len(groups) >= 4:
                    result['max_salary'] = groups[3].replace(',', '')
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
    return jobs_df[mask]


def filter_jobs_by_location(jobs_df: pd.DataFrame, locations: List[str]) -> pd.DataFrame:
    """Filter jobs by location"""
    if jobs_df.empty or not locations:
        return jobs_df
    
    pattern = '|'.join([re.escape(location) for location in locations])
    mask = jobs_df['location'].str.contains(pattern, case=False, na=False)
    return jobs_df[mask]


def add_relevance_score(jobs_df: pd.DataFrame, preferred_keywords: List[str]) -> pd.DataFrame:
    """Add relevance score based on keyword matches"""
    if jobs_df.empty:
        return jobs_df
    
    df = jobs_df.copy()
    df['relevance_score'] = 0
    
    for keyword in preferred_keywords:
        # Check title matches (higher weight)
        title_matches = df['title'].str.contains(keyword, case=False, na=False)
        df.loc[title_matches, 'relevance_score'] += 3
        
        # Check company matches
        if 'company' in df.columns:
            company_matches = df['company'].str.contains(keyword, case=False, na=False)
            df.loc[company_matches, 'relevance_score'] += 1
    
    return df.sort_values('relevance_score', ascending=False)


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
