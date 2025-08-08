"""
Job Scraper Module
Handles web scraping of job postings from various job sites
"""

from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import time
import pandas as pd
from loguru import logger
from ..config.settings import Config
import os
import glob
from pathlib import Path


class JobScraper:
    """Base class for job scraping functionality"""
    
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': config.user_agent
        })
        self.jobs_data: List[Dict] = []
        
    def setup_driver(self) -> webdriver.Chrome:
        """Setup Chrome WebDriver with appropriate options"""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument(f'--user-agent={self.config.user_agent}')
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        return driver
    
    def scrape_indeed(self, keywords: str, location: str) -> List[Dict]:
        """Scrape job postings from Indeed"""
        logger.info(f"Scraping Indeed for '{keywords}' in '{location}'")
        jobs = []
        
        try:
            driver = self.setup_driver()
            
            # Build Indeed search URL
            base_url = "https://www.indeed.com/jobs"
            params = {
                'q': keywords,
                'l': location,
                'sort': 'date'
            }
            
            # Construct URL with parameters
            url = f"{base_url}?q={keywords.replace(' ', '+')}&l={location.replace(' ', '+')}&sort=date"
            driver.get(url)
            
            # Wait for job listings to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-jk]"))
            )
            
            # Find job cards
            job_cards = driver.find_elements(By.CSS_SELECTOR, "[data-jk]")
            
            for card in job_cards[:20]:  # Limit to first 20 results
                try:
                    title_element = card.find_element(By.CSS_SELECTOR, "h2 a span")
                    company_element = card.find_element(By.CSS_SELECTOR, "[data-testid='company-name']")
                    location_element = card.find_element(By.CSS_SELECTOR, "[data-testid='job-location']")
                    
                    job_data = {
                        'title': title_element.text.strip(),
                        'company': company_element.text.strip(),
                        'location': location_element.text.strip(),
                        'source': 'Indeed',
                        'url': card.find_element(By.CSS_SELECTOR, "h2 a").get_attribute('href'),
                        'scraped_at': pd.Timestamp.now()
                    }
                    
                    # Try to get salary if available
                    try:
                        salary_element = card.find_element(By.CSS_SELECTOR, "[data-testid='salary-snippet']")
                        job_data['salary'] = salary_element.text.strip()
                    except:
                        job_data['salary'] = 'Not specified'
                    
                    jobs.append(job_data)
                    
                except Exception as e:
                    logger.warning(f"Error extracting job data: {e}")
                    continue
                    
            driver.quit()
            logger.info(f"Scraped {len(jobs)} jobs from Indeed")
            
        except Exception as e:
            logger.error(f"Error scraping Indeed: {e}")
            
        return jobs
    
    def scrape_linkedin(self, keywords: str, location: str) -> List[Dict]:
        """Scrape job postings from LinkedIn (basic implementation) - last 24 hours, full-time only"""
        logger.info(f"Scraping LinkedIn for '{keywords}' in '{location}' (last 24 hours, full-time)")
        jobs = []
        
        try:
            # LinkedIn requires authentication for full access
            # This is a basic implementation that works for public job listings
            # Adding f_TPR=r86400 parameter for jobs posted in last 24 hours (86400 seconds = 1 day)
            # Adding f_JT=F parameter for full-time jobs only
            url = f"https://www.linkedin.com/jobs/search/?keywords={keywords}&location={location}&f_TPR=r86400&f_JT=F"
            
            response = self.session.get(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # LinkedIn's structure changes frequently, this is a basic example
            job_cards = soup.find_all('div', class_='job-search-card')
            
            for card in job_cards:
                try:
                    title = card.find('h3', class_='base-search-card__title')
                    company = card.find('h4', class_='base-search-card__subtitle')
                    location_elem = card.find('span', class_='job-search-card__location')
                    
                    if title and company:
                        job_data = {
                            'title': title.get_text().strip(),
                            'company': company.get_text().strip(),
                            'location': location_elem.get_text().strip() if location_elem else location,
                            'source': 'LinkedIn',
                            'url': card.find('a')['href'] if card.find('a') else '',
                            'salary': 'Not specified',
                            'scraped_at': pd.Timestamp.now()
                        }
                        jobs.append(job_data)
                        
                except Exception as e:
                    logger.warning(f"Error extracting LinkedIn job data: {e}")
                    continue
                    
            logger.info(f"Scraped {len(jobs)} jobs from LinkedIn (last 24 hours, full-time)")
            
        except Exception as e:
            logger.error(f"Error scraping LinkedIn: {e}")
            
        return jobs
    
    def get_most_recent_csv_file(self) -> Optional[str]:
        """Get the path to the most recent CSV file in the data directory"""
        data_dir = Path("data")
        if not data_dir.exists():
            return None
        
        # Find all CSV files with the pattern jobs_YYYYMMDD_HHMMSS.csv
        pattern = str(data_dir / "jobs_*.csv")
        csv_files = glob.glob(pattern)
        
        if not csv_files:
            return None
        
        # Sort files by modification time (newest first)
        csv_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        
        most_recent = csv_files[0]
        logger.info(f"Found most recent CSV file: {most_recent}")
        return most_recent
    
    def load_historical_jobs(self) -> pd.DataFrame:
        """Load jobs from the most recent CSV file"""
        recent_file = self.get_most_recent_csv_file()
        
        if recent_file is None:
            logger.info("No historical CSV files found")
            return pd.DataFrame()
        
        try:
            historical_df = pd.read_csv(recent_file)
            logger.info(f"Loaded {len(historical_df)} historical jobs from {recent_file}")
            return historical_df
        except Exception as e:
            logger.error(f"Error loading historical CSV file {recent_file}: {e}")
            return pd.DataFrame()
    
    def remove_historical_duplicates(self, current_df: pd.DataFrame) -> pd.DataFrame:
        """Remove jobs that already exist in historical data"""
        if current_df.empty:
            return current_df
        
        # Load historical jobs
        historical_df = self.load_historical_jobs()
        
        if historical_df.empty:
            logger.info("No historical data to compare against")
            return current_df
        
        original_count = len(current_df)
        
        # Create a set of historical job identifiers (title + company)
        if 'title' in historical_df.columns and 'company' in historical_df.columns:
            historical_jobs = set()
            for _, row in historical_df.iterrows():
                job_id = f"{row['title'].strip().lower()}|{row['company'].strip().lower()}"
                historical_jobs.add(job_id)
            
            # Filter out current jobs that match historical ones
            def is_new_job(row):
                current_job_id = f"{row['title'].strip().lower()}|{row['company'].strip().lower()}"
                return current_job_id not in historical_jobs
            
            current_df = current_df[current_df.apply(is_new_job, axis=1)]
            
            filtered_count = len(current_df)
            removed_count = original_count - filtered_count
            
            if removed_count > 0:
                logger.info(f"Removed {removed_count} jobs that were found in previous runs")
            else:
                logger.info("No historical duplicates found - all jobs are new!")
        else:
            logger.warning("Historical data missing required columns (title, company)")
        
        return current_df
    
    def get_all_csv_files(self) -> List[str]:
        """Get all CSV files in the data directory sorted by date (newest first)"""
        data_dir = Path("data")
        if not data_dir.exists():
            return []
        
        # Find all CSV files with the pattern jobs_YYYYMMDD_HHMMSS.csv
        pattern = str(data_dir / "jobs_*.csv")
        csv_files = glob.glob(pattern)
        
        if not csv_files:
            return []
        
        # Sort files by modification time (newest first)
        csv_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        
        return csv_files
    
    def load_all_historical_jobs(self) -> pd.DataFrame:
        """Load and combine all historical job data"""
        csv_files = self.get_all_csv_files()
        
        if not csv_files:
            logger.info("No historical CSV files found")
            return pd.DataFrame()
        
        all_jobs = []
        total_files = len(csv_files)
        
        logger.info(f"Loading jobs from {total_files} CSV files...")
        
        for i, csv_file in enumerate(csv_files):
            try:
                df = pd.read_csv(csv_file)
                all_jobs.append(df)
                logger.debug(f"Loaded {len(df)} jobs from {csv_file} ({i+1}/{total_files})")
            except Exception as e:
                logger.warning(f"Error loading {csv_file}: {e}")
                continue
        
        if not all_jobs:
            logger.warning("No valid CSV files could be loaded")
            return pd.DataFrame()
        
        # Combine all DataFrames
        combined_df = pd.concat(all_jobs, ignore_index=True)
        logger.info(f"Combined {len(combined_df)} total job records from all files")
        
        return combined_df
    
    def load_latest_historical_jobs(self, num_runs: int = 5) -> pd.DataFrame:
        """Load job data from the most recent N CSV files"""
        csv_files = self.get_all_csv_files()
        
        if not csv_files:
            logger.info("No historical CSV files found")
            return pd.DataFrame()
        
        # Take only the most recent N files
        recent_files = csv_files[:num_runs]
        actual_runs = len(recent_files)
        
        logger.info(f"Loading jobs from the most recent {actual_runs} CSV files...")
        
        recent_jobs = []
        
        for i, csv_file in enumerate(recent_files):
            try:
                df = pd.read_csv(csv_file)
                recent_jobs.append(df)
                logger.debug(f"Loaded {len(df)} jobs from {csv_file} ({i+1}/{actual_runs})")
            except Exception as e:
                logger.warning(f"Error loading {csv_file}: {e}")
                continue
        
        if not recent_jobs:
            logger.warning("No valid recent CSV files could be loaded")
            return pd.DataFrame()
        
        # Combine recent DataFrames
        combined_df = pd.concat(recent_jobs, ignore_index=True)
        logger.info(f"Combined {len(combined_df)} job records from {actual_runs} recent files")
        
        return combined_df
    
    def filter_historical_jobs(self) -> pd.DataFrame:
        """Apply current filters to all historical job data and return filtered results"""
        logger.info("Filtering all historical job data...")
        
        # Load all historical jobs
        all_jobs_df = self.load_all_historical_jobs()
        
        if all_jobs_df.empty:
            logger.warning("No historical job data found to filter")
            return pd.DataFrame()
        
        original_count = len(all_jobs_df)
        logger.info(f"Loaded {original_count} total historical jobs")
        
        # Apply current filters
        filtered_df = self.filter_unwanted_jobs(all_jobs_df.copy())
        
        # Remove duplicates
        unique_filtered_df = filtered_df.drop_duplicates(subset=['title', 'company'], keep='first')
        
        final_count = len(unique_filtered_df)
        removed_count = original_count - final_count
        
        logger.info(f"After filtering: {final_count} jobs remaining ({removed_count} jobs removed)")
        
        return unique_filtered_df
    
    def scrape_all_sources(self, keywords: List[str], locations: List[str]) -> pd.DataFrame:
        """Scrape jobs from LinkedIn only"""
        all_jobs = []
        
        for keyword in keywords:
            for location in locations:
                # Add delay between requests
                time.sleep(self.config.request_delay)
                
                # Scrape LinkedIn only
                linkedin_jobs = self.scrape_linkedin(keyword, location)
                all_jobs.extend(linkedin_jobs)
        
        # Convert to DataFrame and process
        df = pd.DataFrame(all_jobs)
        if not df.empty:
            # Filter out jobs with unwanted keywords in title
            df = self.filter_unwanted_jobs(df)
            
            # Remove duplicates within current results
            df = df.drop_duplicates(subset=['title', 'company'], keep='first')
            logger.info(f"Current run found {len(df)} unique jobs after filtering")
            
            # Remove jobs that were found in previous runs
            df = self.remove_historical_duplicates(df)
            
            logger.info(f"Final count after removing historical duplicates: {len(df)}")
        else:
            logger.warning("No jobs found")
            
        return df
    
    def filter_unwanted_jobs(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter out jobs with unwanted keywords in the title"""
        if df.empty:
            return df
            
        original_count = len(df)
        
        # Get unwanted keywords from configuration
        unwanted_keywords = self.config.get_unwanted_keywords_list()
        
        for keyword in unwanted_keywords:
            mask = ~df['title'].str.contains(keyword, case=False, na=False)
            df = df[mask]
        
        filtered_count = len(df)
        removed_count = original_count - filtered_count
        
        if removed_count > 0:
            logger.info(f"Filtered out {removed_count} jobs containing unwanted keywords")
            
        return df
    
    def save_jobs_to_csv(self, df: pd.DataFrame, filename: Optional[str] = None) -> str:
        """Save jobs data to CSV file"""
        if filename is None:
            filename = f"jobs_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        filepath = f"data/{filename}"
        df.to_csv(filepath, index=False)
        logger.info(f"Jobs data saved to {filepath}")
        return filepath
