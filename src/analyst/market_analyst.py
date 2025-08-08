"""
Market Analyst Module
Handles AI-powered job market analysis using Google Gemini
"""

from typing import Dict, List, Optional
import pandas as pd
from loguru import logger
import json
from datetime import datetime, timedelta
import requests
from ..config.settings import Config


class MarketAnalyst:
    """AI-powered job market analyst using Google Gemini"""
    
    def __init__(self, config: Config):
        self.config = config
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.gemini_model}:generateContent"
        self.headers = {
            'Content-Type': 'application/json'
        }
        
    def _prepare_job_data_for_analysis(self, jobs_df: pd.DataFrame) -> str:
        """Prepare job data in a format suitable for AI analysis"""
        if jobs_df.empty:
            return "No job data available for analysis."
        
        # Get basic statistics
        total_jobs = len(jobs_df)
        unique_companies = jobs_df['company'].nunique() if 'company' in jobs_df.columns else 0
        unique_locations = jobs_df['location'].nunique() if 'location' in jobs_df.columns else 0
        
        # Create summary statistics
        summary = {
            "total_jobs": total_jobs,
            "unique_companies": unique_companies,
            "unique_locations": unique_locations,
            "date_range": self._get_date_range(jobs_df),
            "top_companies": self._get_top_companies(jobs_df, limit=10),
            "top_locations": self._get_top_locations(jobs_df, limit=10),
            "job_titles_sample": self._get_job_titles_sample(jobs_df, limit=20),
            "salary_info": self._get_salary_info(jobs_df)
        }
        
        # Convert to formatted string
        analysis_text = f"""
JOB MARKET DATA SUMMARY:

OVERVIEW:
- Total Jobs Analyzed: {summary['total_jobs']}
- Unique Companies: {summary['unique_companies']}
- Unique Locations: {summary['unique_locations']}
- Date Range: {summary['date_range']}

TOP COMPANIES (by number of job postings):
{self._format_top_list(summary['top_companies'])}

TOP LOCATIONS (by number of job postings):
{self._format_top_list(summary['top_locations'])}

JOB TITLES SAMPLE:
{self._format_job_titles(summary['job_titles_sample'])}

SALARY INFORMATION:
{summary['salary_info']}
"""
        
        return analysis_text
    
    def _get_date_range(self, jobs_df: pd.DataFrame) -> str:
        """Get the date range of scraped jobs"""
        if 'scraped_at' not in jobs_df.columns:
            return "Date information not available"
        
        try:
            dates = pd.to_datetime(jobs_df['scraped_at'])
            min_date = dates.min().strftime('%Y-%m-%d')
            max_date = dates.max().strftime('%Y-%m-%d')
            
            if min_date == max_date:
                return f"Single day: {min_date}"
            else:
                return f"{min_date} to {max_date}"
        except Exception as e:
            logger.warning(f"Error processing date range: {e}")
            return "Date information not available"
    
    def _get_top_companies(self, jobs_df: pd.DataFrame, limit: int = 10) -> List[Dict]:
        """Get top companies by job count"""
        if 'company' not in jobs_df.columns:
            return []
        
        try:
            company_counts = jobs_df['company'].value_counts().head(limit)
            return [{'name': company, 'count': count} for company, count in company_counts.items()]
        except Exception as e:
            logger.warning(f"Error processing company data: {e}")
            return []
    
    def _get_top_locations(self, jobs_df: pd.DataFrame, limit: int = 10) -> List[Dict]:
        """Get top locations by job count"""
        if 'location' not in jobs_df.columns:
            return []
        
        try:
            location_counts = jobs_df['location'].value_counts().head(limit)
            return [{'name': location, 'count': count} for location, count in location_counts.items()]
        except Exception as e:
            logger.warning(f"Error processing location data: {e}")
            return []
    
    def _get_job_titles_sample(self, jobs_df: pd.DataFrame, limit: int = 20) -> List[str]:
        """Get a sample of job titles"""
        if 'title' not in jobs_df.columns:
            return []
        
        try:
            # Get unique titles, limited to the specified number
            unique_titles = jobs_df['title'].drop_duplicates().head(limit).tolist()
            return unique_titles
        except Exception as e:
            logger.warning(f"Error processing job titles: {e}")
            return []
    
    def _get_salary_info(self, jobs_df: pd.DataFrame) -> str:
        """Get salary information summary"""
        if 'salary' not in jobs_df.columns:
            return "Salary information not available"
        
        try:
            salary_counts = jobs_df['salary'].value_counts()
            total_jobs = len(jobs_df)
            
            # Count jobs with actual salary info vs "Not specified"
            jobs_with_salary = salary_counts.drop('Not specified', errors='ignore').sum()
            jobs_without_salary = salary_counts.get('Not specified', 0)
            
            info = f"Jobs with salary info: {jobs_with_salary} ({jobs_with_salary/total_jobs*100:.1f}%)\n"
            info += f"Jobs without salary info: {jobs_without_salary} ({jobs_without_salary/total_jobs*100:.1f}%)"
            
            # Show top salary ranges if available
            if jobs_with_salary > 0:
                top_salaries = salary_counts.drop('Not specified', errors='ignore').head(5)
                info += "\n\nMost common salary ranges:\n"
                for salary, count in top_salaries.items():
                    info += f"- {salary}: {count} jobs\n"
            
            return info
        except Exception as e:
            logger.warning(f"Error processing salary data: {e}")
            return "Error processing salary information"
    
    def _format_top_list(self, top_list: List[Dict]) -> str:
        """Format top companies/locations list"""
        if not top_list:
            return "No data available"
        
        formatted = ""
        for i, item in enumerate(top_list, 1):
            formatted += f"{i}. {item['name']}: {item['count']} jobs\n"
        
        return formatted.strip()
    
    def _format_job_titles(self, titles: List[str]) -> str:
        """Format job titles list"""
        if not titles:
            return "No job titles available"
        
        formatted = ""
        for i, title in enumerate(titles, 1):
            formatted += f"{i}. {title}\n"
        
        return formatted.strip()
    
    def _make_gemini_request(self, prompt: str) -> Optional[str]:
        """Make a request to Google Gemini API"""
        try:
            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ]
            }
            
            # Add API key as query parameter
            url_with_key = f"{self.api_url}?key={self.config.gemini_api_key}"
            
            response = requests.post(
                url_with_key,
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Extract text from Gemini response
                if 'candidates' in result and len(result['candidates']) > 0:
                    candidate = result['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content']:
                        parts = candidate['content']['parts']
                        if len(parts) > 0 and 'text' in parts[0]:
                            return parts[0]['text']
                
                logger.warning("Unexpected response format from Gemini API")
                return None
            else:
                logger.error(f"Gemini API error: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error calling Gemini API: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error calling Gemini API: {e}")
            return None
    
    def generate_market_report(self, jobs_df: pd.DataFrame) -> Optional[str]:
        """Generate a comprehensive job market report using AI analysis"""
        logger.info("Generating AI-powered job market report...")
        
        if jobs_df.empty:
            logger.warning("No job data available for analysis")
            return None
        
        # Prepare the data for analysis
        job_data_text = self._prepare_job_data_for_analysis(jobs_df)
        
        # Create the full prompt
        full_prompt = f"{self.config.gemini_analysis_prompt}\n\n{job_data_text}"
        
        # Make the AI request
        analysis_result = self._make_gemini_request(full_prompt)
        
        if analysis_result:
            logger.info("Job market report generated successfully")
            return analysis_result
        else:
            logger.error("Failed to generate job market report")
            return None
    
    def test_api_connection(self) -> bool:
        """Test the connection to Gemini API"""
        logger.info("Testing Gemini API connection...")
        
        test_prompt = "Hello! Please respond with 'API connection successful' to confirm the connection is working."
        
        result = self._make_gemini_request(test_prompt)
        
        if result:
            logger.info("Gemini API connection test successful")
            logger.debug(f"API response: {result[:100]}...")
            return True
        else:
            logger.error("Gemini API connection test failed")
            return False
    
    def get_analysis_summary(self) -> Dict:
        """Get summary of analyzer configuration"""
        return {
            'api_configured': bool(self.config.gemini_api_key),
            'model': self.config.gemini_model,
            'prompt_length': len(self.config.gemini_analysis_prompt)
        }
