#!/usr/bin/env python3
"""
Job Informer Dashboard
Web-based dashboard for visualizing job statistics from parsed descriptions
"""

import sys
import json
import sqlite3
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional

import pandas as pd
from flask import Flask, render_template, jsonify, request
from flask import make_response

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.utils.database import JobDatabase

app = Flask(__name__)


class DashboardData:
    """Data processor for dashboard statistics with simple in-memory caching and filtering."""

    def __init__(self, db_path: str = "data/jobs.db"):
        self.db_path = db_path
        self.db = JobDatabase(db_path)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl_seconds = 120  # 2 minutes default

    def _cache_get(self, key: str) -> Optional[pd.DataFrame]:
        rec = self._cache.get(key)
        if not rec:
            return None
        if (pd.Timestamp.utcnow() - rec['ts']).total_seconds() > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return rec['df']

    def _cache_put(self, key: str, df: pd.DataFrame) -> None:
        self._cache[key] = {'df': df, 'ts': pd.Timestamp.utcnow()}

    def get_parsed_data(self) -> pd.DataFrame:
        """Load & parse combined jobs + parsed_descriptions (cached)."""
        key = 'parsed_all'
        cached = self._cache_get(key)
        if cached is not None:
            return cached.copy()
        with sqlite3.connect(self.db_path) as conn:
            query = """
            SELECT 
                j.job_id, j.title, j.company, j.location, j.source, 
                j.salary, j.scraped_at, p.payload_json, p.created_at as parsed_at
            FROM jobs j
            LEFT JOIN parsed_descriptions p ON j.job_id = p.job_id
            ORDER BY j.scraped_at DESC
            """
            df = pd.read_sql_query(query, conn)
        if df.empty:
            self._cache_put(key, df)
            return df
        # Normalize times
        for col in ['scraped_at', 'parsed_at']:
            if col in df.columns:
                try:
                    df[col] = pd.to_datetime(df[col], errors='coerce')
                except Exception:
                    pass
        parsed_rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            try:
                payload = json.loads(row['payload_json']) if row.get('payload_json') else {}
            except Exception:
                payload = {}
            base = row.to_dict()
            # Merge only if keys not already present
            for k, v in payload.items():
                base[k] = v
            parsed_rows.append(base)
        out = pd.DataFrame(parsed_rows)
        self._cache_put(key, out)
        return out.copy()

    def filter_df(self, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        if df.empty:
            return df
        f = df
        # Date range
        start_date = params.get('start_date')
        end_date = params.get('end_date')
        if start_date and 'scraped_at' in f.columns:
            try:
                sd = pd.to_datetime(start_date)
                f = f[f['scraped_at'] >= sd]
            except Exception:
                pass
        if end_date and 'scraped_at' in f.columns:
            try:
                ed = pd.to_datetime(end_date) + pd.Timedelta(days=1)
                f = f[f['scraped_at'] < ed]
            except Exception:
                pass
        # Company filter (substring, case-insensitive)
        company = params.get('company')
        if company and 'company' in f.columns:
            f = f[f['company'].astype(str).str.contains(company, case=False, na=False)]
        # Location filter
        location = params.get('location')
        if location and 'location' in f.columns:
            f = f[f['location'].astype(str).str.contains(location, case=False, na=False)]
        # Keyword in title
        keyword = params.get('keyword')
        if keyword and 'title' in f.columns:
            f = f[f['title'].astype(str).str.contains(keyword, case=False, na=False)]
        # Min / max experience
        try:
            min_exp = params.get('min_exp')
            if min_exp not in (None, '') and 'years_experience_min' in f.columns:
                f = f[pd.to_numeric(f['years_experience_min'], errors='coerce') >= float(min_exp)]
        except Exception:
            pass
        try:
            max_exp = params.get('max_exp')
            if max_exp not in (None, '') and 'years_experience_min' in f.columns:
                f = f[pd.to_numeric(f['years_experience_min'], errors='coerce') <= float(max_exp)]
        except Exception:
            pass
        return f
    
    def get_seniority_stats(self, df: pd.DataFrame) -> Dict[str, int]:
        """Get seniority level statistics"""
        if 'seniority' not in df.columns:
            return {}
        return df['seniority'].value_counts().sort_values(ascending=False).to_dict()
    
    def get_employment_type_stats(self, df: pd.DataFrame) -> Dict[str, int]:
        """Get employment type statistics"""
        if 'employment_type' not in df.columns:
            return {}
        return df['employment_type'].value_counts().sort_values(ascending=False).to_dict()
    
    def get_remote_stats(self, df: pd.DataFrame) -> Dict[str, int]:
        """Get remote work statistics"""
        if 'remote' not in df.columns:
            return {}
        return df['remote'].value_counts().sort_values(ascending=False).to_dict()
    
    def get_top_programming_languages(self, df: pd.DataFrame, top_n: int = 10) -> Dict[str, int]:
        """Get top programming languages"""
        if 'programming_languages' not in df.columns:
            return {}
        
        lang_counter = Counter()
        for _, row in df.iterrows():
            langs = row.get('programming_languages', [])
            if isinstance(langs, list):
                lang_counter.update(langs)
        
        return dict(lang_counter.most_common(top_n))
    
    def get_top_tools(self, df: pd.DataFrame, top_n: int = 15) -> Dict[str, int]:
        """Get top tools/technologies"""
        if 'tools' not in df.columns:
            return {}
        
        tool_counter = Counter()
        for _, row in df.iterrows():
            tools = row.get('tools', [])
            if isinstance(tools, list):
                tool_counter.update(tools)
        
        return dict(tool_counter.most_common(top_n))
    
    def get_top_skills(self, df: pd.DataFrame, top_n: int = 15) -> Dict[str, int]:
        """Get top skills"""
        if 'skills' not in df.columns:
            return {}
        
        skill_counter = Counter()
        for _, row in df.iterrows():
            skills = row.get('skills', [])
            if isinstance(skills, list):
                skill_counter.update(skills)
        
        return dict(skill_counter.most_common(top_n))
    
    def get_degree_stats(self, df: pd.DataFrame) -> Dict[str, Dict[str, int]]:
        """Get degree field and type statistics"""
        stats = {}
        
        if 'degree_field' in df.columns:
            stats['field'] = df['degree_field'].value_counts().sort_values(ascending=False).to_dict()
        
        if 'degree_type' in df.columns:
            stats['type'] = df['degree_type'].value_counts().sort_values(ascending=False).to_dict()
        
        return stats
    
    def get_salary_stats(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Get salary statistics plus histogram buckets if possible."""
        if 'salary_eur_range' not in df.columns:
            return {}
        pairs = []  # collect midpoint estimates
        vals = []   # raw min/max points for simple stats
        for _, row in df.iterrows():
            rng = row.get('salary_eur_range')
            if isinstance(rng, dict):
                lo = rng.get('min')
                hi = rng.get('max')
                if isinstance(lo, (int, float)):
                    vals.append(lo)
                if isinstance(hi, (int, float)):
                    vals.append(hi)
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                    pairs.append((lo + hi) / 2)
                elif isinstance(lo, (int, float)):
                    pairs.append(lo)
                elif isinstance(hi, (int, float)):
                    pairs.append(hi)
        if not vals:
            return {}
        vals_sorted = sorted(vals)
        median = vals_sorted[len(vals_sorted)//2]
        avg = sum(vals_sorted) / len(vals_sorted)
        salary_stats = {
            'count': len(vals_sorted),
            'min': min(vals_sorted),
            'max': max(vals_sorted),
            'median': median,
            'avg': avg
        }
        # Histogram buckets (EUR) if we have at least 8 points
        if len(pairs) >= 8:
            try:
                import math
                low = math.floor(min(pairs) / 5000) * 5000
                high = math.ceil(max(pairs) / 5000) * 5000
                if high == low:
                    high = low + 5000
                buckets = list(range(int(low), int(high)+1, 5000))
                bucket_labels = []
                bucket_counts = []
                for i in range(len(buckets)-1):
                    a, b = buckets[i], buckets[i+1]
                    cnt = sum(1 for v in pairs if a <= v < b or (i == len(buckets)-2 and v == b))
                    bucket_labels.append(f"{a//1000}k-{b//1000}k")
                    bucket_counts.append(cnt)
                salary_stats['histogram'] = dict(zip(bucket_labels, bucket_counts))
            except Exception:
                pass
        return salary_stats
    
    def get_location_stats(self, df: pd.DataFrame, top_n: int = 10) -> Dict[str, int]:
        """Get top job locations"""
        if 'location' not in df.columns:
            return {}
        
        # Handle both parsed location (list) and original location (string)
        loc_counter = Counter()
        for _, row in df.iterrows():
            # Try parsed location first
            parsed_locs = row.get('location')  # This is from parsed JSON
            if isinstance(parsed_locs, list):
                loc_counter.update(parsed_locs)
            else:
                # Fall back to original location column
                orig_loc = row.get('location')  # This might be the original location field
                if isinstance(orig_loc, str) and orig_loc.strip():
                    loc_counter[orig_loc.strip()] += 1
        
        return dict(loc_counter.most_common(top_n))
    
    def get_company_stats(self, df: pd.DataFrame, top_n: int = 10) -> Dict[str, int]:
        """Get top companies"""
        if 'company' not in df.columns:
            return {}
        return df['company'].value_counts().sort_values(ascending=False).head(top_n).to_dict()
    
    def get_experience_stats(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Get experience requirements statistics"""
        if 'years_experience_min' not in df.columns:
            return {}
        
        exp_values = []
        for _, row in df.iterrows():
            exp = row.get('years_experience_min')
            if isinstance(exp, (int, float)) and exp >= 0:
                exp_values.append(exp)
        
        if not exp_values:
            return {}
        
        # Sort experience distribution by count (descending)
        exp_dist = Counter(exp_values)
        sorted_dist = dict(exp_dist.most_common())
        
        return {
            'count': len(exp_values),
            'min': min(exp_values),
            'max': max(exp_values),
            'avg': sum(exp_values) / len(exp_values),
            'distribution': sorted_dist
        }

    def get_jobs_per_day(self, df: pd.DataFrame, days: int = 30) -> Dict[str, int]:
        if df.empty or 'scraped_at' not in df.columns:
            return {}
        tmp = df.dropna(subset=['scraped_at']).copy()
        if tmp.empty:
            return {}
        tmp['day'] = tmp['scraped_at'].dt.date
        last_n = sorted(tmp['day'].unique())[-days:]
        counts = tmp[tmp['day'].isin(last_n)]['day'].value_counts().sort_index()
        return {str(k): int(v) for k, v in counts.items()}

    def get_growth_rate(self, df: pd.DataFrame) -> Optional[float]:
        """Compute growth rate between last 7 days and previous 7 days ( (recent-prev)/max(prev,1) )."""
        series = self.get_jobs_per_day(df, days=14)
        if not series or len(series) < 8:
            return None
        items = list(series.items())
        # items are chronological because get_jobs_per_day returns sorted keys
        recent = sum(v for _, v in items[-7:])
        prev = sum(v for _, v in items[-14:-7])
        if prev == 0:
            return None
        return (recent - prev) / prev


# Global data instance
dashboard_data = DashboardData()


@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')


@app.route('/api/stats')
def api_stats():
    """API endpoint for dashboard statistics (supports filtering via query params)."""
    try:
        params = {k: v for k, v in request.args.items() if v not in (None, '')}
        df = dashboard_data.get_parsed_data()
        if df.empty:
            return jsonify({'error': 'No parsed job data found', 'total_jobs': 0})
        filtered = dashboard_data.filter_df(df, params)
        if filtered.empty:
            return jsonify({'warning': 'No data after filters', 'total_jobs': 0})
        stats = {
            'total_jobs': int(len(filtered)),
            'seniority': dashboard_data.get_seniority_stats(filtered),
            'employment_type': dashboard_data.get_employment_type_stats(filtered),
            'remote': dashboard_data.get_remote_stats(filtered),
            'programming_languages': dashboard_data.get_top_programming_languages(filtered),
            'tools': dashboard_data.get_top_tools(filtered),
            'skills': dashboard_data.get_top_skills(filtered),
            'degrees': dashboard_data.get_degree_stats(filtered),
            'salary': dashboard_data.get_salary_stats(filtered),
            'locations': dashboard_data.get_location_stats(filtered),
            'companies': dashboard_data.get_company_stats(filtered),
            'experience': dashboard_data.get_experience_stats(filtered),
            'jobs_per_day': dashboard_data.get_jobs_per_day(filtered),
            'last_updated': str(filtered['scraped_at'].max()) if 'scraped_at' in filtered.columns else None,
            'applied_filters': params,
            'trends': {
                'growth_rate_7d': dashboard_data.get_growth_rate(filtered)
            }
        }
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/raw')
def api_raw():
    """Return raw rows (limited) for inspection & debugging."""
    try:
        limit = int(request.args.get('limit', 50))
        df = dashboard_data.get_parsed_data().head(max(1, min(limit, 500)))
        return jsonify(df.to_dict(orient='records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/export')
def api_export():
    """Export filtered data as CSV or JSON (default JSON)."""
    try:
        params = {k: v for k, v in request.args.items() if v not in (None, '')}
        fmt = params.pop('format', 'json').lower()
        limit = int(params.pop('limit', '0') or 0)
        df = dashboard_data.get_parsed_data()
        if df.empty:
            return jsonify([])
        f = dashboard_data.filter_df(df, params)
        if limit > 0:
            f = f.head(limit)
        if fmt == 'csv':
            csv_data = f.to_csv(index=False)
            resp = make_response(csv_data)
            resp.headers['Content-Type'] = 'text/csv'
            resp.headers['Content-Disposition'] = 'attachment; filename="jobs_export.csv"'
            return resp
        # default json
        return jsonify(f.to_dict(orient='records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description="Job Informer Dashboard")
    parser.add_argument('--port', type=int, default=5000, help='Port to run dashboard on')
    parser.add_argument('--host', type=str, default='127.0.0.1', help='Host to bind to')
    parser.add_argument('--debug', action='store_true', help='Run in debug mode')
    
    args = parser.parse_args()
    
    print(f"Starting Job Informer Dashboard at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
