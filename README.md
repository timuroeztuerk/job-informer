# Job Informer 🔍

An automated job monitoring and notification system that scrapes job postings from multiple sources and sends email reports.

## Features

- **LinkedIn Job Scraping**: Public listings with 24-hour and full-time filtering
- **AI-Powered Market Analysis**: Generate comprehensive job market reports using Google Gemini
- **Manual Job Search Execution**: Run job searches on-demand when you need them
- **Smart Historical Data Management**: Track historical jobs with duplicate prevention
- **Flexible Filtering System**: Configurable unwanted keywords filtering via environment variables
- **Multiple Execution Modes**: Run-once, summaries, historical filtering, and AI analysis
- **Beautiful Email Reports**: HTML-formatted email notifications with job details and AI insights
- **Data Export**: Jobs saved to CSV with timestamp tracking
- **Smart Filtering**: Keyword-based filtering and duplicate removal across runs
- **Robust Error Handling**: Comprehensive logging and error notifications
- **Configurable**: Environment-based configuration for easy deployment

## Quick Start

### 1. Installation

```bash
# Set up the project (create venv, install deps, and copy .env.example)
python -m venv .venv
source .venv/bin/activate  # or .venv\\Scripts\\activate on Windows
pip install -r requirements.txt
cp .env.example .env
```

### 2. Configuration

Edit `.env` with your configuration:

```env
# Email Configuration
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
EMAIL_ADDRESS=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
RECIPIENT_EMAIL=your_recipient@gmail.com

# Job Search Configuration
SEARCH_KEYWORDS=Data Scientist,Software Engineer
SEARCH_LOCATIONS=Stuttgart,Remote
UNWANTED_KEYWORDS=internship,sales,freelance,werkstudent,intern,trainee,thesis,student,part-time,lecturer,tester,manager
UNWANTED_COMPANIES=amazon,recruiter gmbh,acme staffing

# AI Analysis Configuration
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash-exp
GEMINI_ANALYSIS_PROMPT=Analyze the following job market data and provide insights on trends, salary ranges, required skills, company types, and market opportunities. Focus on actionable insights for job seekers.
```

### 3. Test Your Setup

```bash
python main.py --mode quick-test
```

### 4. Run Job Search

```bash
# Run job search once (default)
python main.py

# Or explicitly
python main.py --mode run-once
```

## Usage Examples

### Available Modes

- **`run-once`** (default): Execute job search immediately and send results via email
- **`test`**: Run combined tests (config/email, scraper init, AI connection)
- **`summary`**: Send a summary of recent jobs (limited to 50 most recent)
- **`market-report`**: Generate and send AI-powered job market analysis report
- **`db-summary`**: Show a summary of the database
- **`migrate-csv`**: Migrate CSV files to the database
- **`purge`**: Remove duplicates and jobs matching UNWANTED_KEYWORDS or UNWANTED_COMPANIES from the database

### Command Examples

```bash
# Basic job search
python main.py

# Run combined tests
python main.py --mode test

# Search with custom keywords and locations
python main.py --keywords "Data Analyst" --locations "Berlin, Remote"

# Send a summary of recent jobs (up to 50)
python main.py --mode summary

# Use custom config file
python main.py --config /path/to/custom/.env

# Migrate CSV files to the database (manual)
python main.py --mode migrate-csv

# Or enable automatic CSV migration on startup
export AUTO_MIGRATE_CSV=true
python main.py --mode run-once
```

### Advanced Usage

```python
from src.config.settings import Config
from src.scrapers.job_scraper import JobScraper
from src.email_notifier.email_sender import EmailSender
from src.analyst.market_analyst import MarketAnalyst

# Load configuration
config = Config.from_env()

# Initialize components
scraper = JobScraper(config)
analyst = MarketAnalyst(config)

# Search for jobs
jobs_df = scraper.scrape_all_sources(
    keywords=['Data Analyst', 'Data Scientist'],
    locations=['Berlin', 'Remote']
)

# Generate AI market analysis
market_report = analyst.generate_market_report(jobs_df)

# Send email report
email_sender = EmailSender(config)
email_sender.send_job_report(jobs_df, "Custom Job Report")
email_sender.send_market_report(market_report, "Market Analysis", len(jobs_df))
```

## Project Structure

```
job-informer/
├── .github/
│   └── copilot-instructions.md
├── src/
│   ├── analyst/
│   │   ├── __init__.py
│   │   └── market_analyst.py    # AI-powered market analysis
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py          # Configuration management
│   ├── scrapers/
│   │   ├── __init__.py
│   │   └── job_scraper.py       # Web scraping logic (headline-only; descriptions via backfill)
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── task_scheduler.py    # Task scheduling
│   ├── email_notifier/
│   │   ├── __init__.py
│   │   └── email_sender.py      # Email notifications
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logging_utils.py     # Logging setup
│   │   └── data_utils.py        # Data processing
│   └── __init__.py
├── data/                        # Job data exports
├── logs/                        # Application logs
├── main.py                      # Main application entry
├── requirements.txt             # Python dependencies
├── .env.example                 # Example configuration
├── .gitignore                   # Git ignore rules
└── README.md                    # This file
```

## Configuration Options

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `SMTP_SERVER` | SMTP server hostname | `smtp.gmail.com` | No |
| `SMTP_PORT` | SMTP server port | `587` | No |
| `EMAIL_ADDRESS` | Your email address | - | Yes |
| `EMAIL_PASSWORD` | Email app password | - | Yes |
| `RECIPIENT_EMAIL` | Recipient email | - | Yes |
| `SEARCH_KEYWORDS` | Job keywords (comma-separated) | `Data` | No |
| `SEARCH_LOCATIONS` | Search locations (comma-separated) | `Stuttgart` | No |
| `UNWANTED_KEYWORDS` | Keywords to filter out (comma-separated) | `internship,sales,freelance...` | No |
| `UNWANTED_COMPANIES` | Company names to filter out (comma-separated, case-insensitive substring match) | - | No |
| `GEMINI_API_KEY` | Google Gemini API key for AI analysis | - | Yes (for AI features) |
| `GEMINI_MODEL` | Gemini model to use | `gemini-2.0-flash-exp` | No |
| `GEMINI_ANALYSIS_PROMPT` | Custom prompt for AI analysis | Default analysis prompt | No |
| `REQUEST_DELAY` | Delay between requests (seconds) | `2.0` | No |
| `MAX_RETRIES` | Maximum retry attempts | `3` | No |
| `SCHEDULE_TIME` | Daily schedule time (HH:MM) | `09:00` | No |
| `LOG_LEVEL` | Logging level | `INFO` | No |
| `LOG_FILE` | Log file path | `logs/job_informer.log` | No |
| `AUTO_MIGRATE_CSV` | Migrate existing CSVs to SQLite automatically on startup | `false` | No |

### Email Setup (Gmail)

1. Enable 2-Factor Authentication in your Google account
2. Generate an App Password:
   - Go to Google Account settings
   - Security → 2-Step Verification → App passwords
   - Generate password for "Mail"
3. Use the generated password in `EMAIL_PASSWORD`

### AI Analysis Setup (Google Gemini)

1. Get a Gemini API key:
   - Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
   - Create a new API key
2. Add the API key to your `.env` file as `GEMINI_API_KEY`
3. Optionally customize the analysis prompt in `GEMINI_ANALYSIS_PROMPT`

## Features in Detail

### Web Scraping
- **LinkedIn Focus**: Specialized scraping with 24-hour and full-time job filtering
- **Respectful Scraping**: Built-in delays and retry logic
- **Historical Tracking**: Advanced duplicate detection across multiple runs
- **Smart Filtering**: Environment-configurable unwanted keyword filtering
- **Data Cleaning**: Automatic duplicate removal and data standardization
- **Error Resilience**: Continues operation even when scraping encounters issues

### AI-Powered Analysis
- **Market Reports**: Comprehensive job market analysis using Google Gemini
- **Trend Insights**: AI-generated insights on job trends, salaries, and opportunities
- **Customizable Prompts**: Configure analysis focus via environment variables
- **Historical Analysis**: Generate reports from all collected historical data
- **Professional Output**: Well-formatted reports suitable for strategic planning

### Email Notifications
- **HTML Reports**: Beautiful, responsive email templates for job listings
- **AI Analysis Reports**: Dedicated templates for market analysis results
- **Plain Text Fallback**: Automatic plain text version for compatibility
- **Job Details**: Title, company, location, salary, and direct links
- **Summary Statistics**: Total jobs, sources, and search parameters
- **Error Alerts**: Automatic notifications when scraping or AI analysis fails

### Historical Data Management
- **Multi-run Tracking**: Compare jobs across multiple search sessions
- **Duplicate Prevention**: Never get notified about the same job twice
- **Summary Reports**: Generate insights from recent runs or all historical data
- **Retroactive Filtering**: Apply new filtering criteria to historical data
- **Data Persistence**: CSV storage with timestamp tracking

### Scheduling
- **Flexible Execution**: One-time, manual execution, or scheduled operation
- **Multiple Modes**: Job search, summaries, AI analysis, and testing modes
- **Background Operation**: Can be integrated with cron jobs or task schedulers
- **Graceful Operation**: Clean execution with comprehensive logging

### Data Management
- **CSV Export**: Timestamped job data with comprehensive tracking
- **Historical Analysis**: Load and analyze data from multiple search sessions
- **Data Cleaning**: Standardization and filtering across search runs
- **Organized Storage**: Timestamped files in data directory for easy management

## Deployment Options

### Local Development
```bash
python main.py --mode schedule
```

### Cron Job (Linux/macOS)
```bash
# Add to crontab for daily 9 AM execution
0 9 * * * cd /path/to/job-informer && python main.py --mode run-once
```

### Docker (Coming Soon)
```
Docker support planned
```

### Cloud Deployment
- AWS Lambda with CloudWatch Events
- Google Cloud Functions with Cloud Scheduler
- Heroku with Scheduler add-on

## Troubleshooting

### Common Issues

**Email Authentication Failed**
```bash
# Test email configuration
python main.py --mode test-email
```
- Verify app password (not regular password)
- Check 2FA is enabled
- Confirm SMTP settings

**No Jobs Found**
- Check keywords and locations in `.env`
- Verify LinkedIn is accessible
- Review logs for scraping errors
- Try broader keywords or different locations

**AI Analysis Failed**
```bash
# Test AI API connection
python main.py --mode test-ai
```
- Verify Gemini API key is correct
- Check API key has proper permissions
- Ensure internet connection is stable
- Review API usage limits

**Historical Data Issues**
- Check if CSV files exist in `data/` directory
- Verify file permissions for reading historical data
- Review logs for file processing errors

**Scraping Errors**
- LinkedIn structure changes frequently
- Check logs for specific errors
- Consider increasing `REQUEST_DELAY` for rate limiting
- Verify network connectivity and firewall settings

### Debug Mode
```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
python main.py --mode run-once
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

### Adding New Features

To extend functionality:

1. **New Job Sites**: Extend the `JobScraper` class in `src/scrapers/job_scraper.py`
2. **Custom Analysis**: Modify the `MarketAnalyst` class in `src/analyst/market_analyst.py`
3. **Email Templates**: Update email templates in `src/email_notifier/email_sender.py`
4. **New Execution Modes**: Add modes in `main.py` and corresponding methods in `TaskScheduler`

### Configuration Guidelines

- Use environment variables for all sensitive data
- Keep filtering keywords updated in `UNWANTED_KEYWORDS`
- Customize AI analysis prompts for your specific needs
- Adjust `REQUEST_DELAY` based on your usage patterns

## License

This project is licensed under the MIT License. See LICENSE file for details.

## Support

For issues, questions, or contributions:
- Create an issue on GitHub
- Check the logs in `logs/job_informer.log`
- Review the configuration in `.env`

---

**Happy Job Hunting! 🚀**
