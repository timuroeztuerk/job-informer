# Job Informer 🔍

An automated job monitoring and notification system that scrapes job postings, analyzes market trends with AI, and sends daily email reports.

## Features

- **Automated Job Scraping**: Monitors LinkedIn for new job postings.
- **AI-Powered Market Analysis**: Uses Google Gemini to generate insights on job trends.
- **Description Parser**: Incrementally parses job descriptions into structured JSON data for analysis.
- **Smart Data Management**: Avoids duplicate job notifications and stores historical data.
- **Customizable Email Reports**: Delivers daily summaries and market analysis to your inbox.
- **Flexible Configuration**: Easily configure search terms, filters, and email settings via a `.env` file.
- **Multiple Execution Modes**: Run on-demand, get summaries, or generate market reports from the command line.

## Installation

1.  **Set up the environment:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # or .venv\Scripts\activate on Windows
    pip install -r requirements.txt
    ```

2.  **Configure the application:**
    -   Copy `.env.example` to `.env`.
    -   Fill in your details for email, job search, and the Gemini API.

## Usage

-   **Run a job search once:**
    ```bash
    python main.py --mode run-once
    ```

-   **Get a summary of recent jobs:**
    ```bash
    python main.py --mode summary
    ```

-   **Generate an AI market report:**
    ```bash
    python main.py --mode market-report
    ```

-   **See all available commands:**
    ```bash
    python main.py --help
    ```

## Configuration

Your `.env` file is the central place for configuration. Here are the key settings:

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
```
*For more options, see `src/config/settings.py`.*

## Development

Contributions are welcome! Please fork the repository, create a feature branch, and submit a pull request.

## License

This project is licensed under the MIT License.

---

## AI-Purger Mode

**Added: August 23, 2025**

The AI-Purger mode is an advanced feature that uses Large Language Models (LLMs) to intelligently identify and remove unwanted job postings from the database. This complements the existing keyword-based filtering with AI-powered analysis.

### Features
- **Batch Processing**: Processes jobs in batches of 50 for efficiency and API rate limiting
- **Smart Analysis**: Uses Google Gemini AI to analyze job titles, companies, and other data
- **Safety Mechanisms**: Built-in safeguards prevent accidental mass deletion
- **Progress Tracking**: Detailed logging of batch processing progress

### Usage
```bash
# Run AI-powered job purging
python main.py --mode ai-purge
```

### Configuration
- Requires `GEMINI_API_KEY` in your `.env` file
- PURGE_AI prompt can be configured in `src/ai_job_purger/ai_purger.py`
- Currently uses empty prompt for safety (returns 0 jobs to purge)

### Example Output
```
=== Starting AI-Powered Job Purging ===
Will process 1170 jobs in 24 batches of 50
Processing batch 1/24 (50 jobs)
Batch 1 identified 2 jobs for purging
...
AI Purge Summary:
  - Jobs analyzed: 1170
  - Batches processed: 24
  - Jobs marked for purging: 45
  - Jobs actually purged: 45
```

### Safety Features
- Empty prompt protection (won't purge if prompt is empty)
- 50% safety limit (won't purge more than half of all jobs)
- Batch validation (ensures returned job IDs exist in the batch)
- LLM connection testing before processing

For detailed documentation, see `AI_PURGER_DOCS.md`.

---

## Performance Fix - August 23, 2025

**Issue**: The description parser was taking an extremely long time or hanging indefinitely when processing jobs, even with small batch sizes (25) and limited batches (1).

**Root Cause**: The original algorithm used an inefficient windowing approach that would:
- Scan large windows of jobs (batch_size × 5 = 125 jobs)  
- Get stuck in loops when all jobs in a window were already cached
- Use complex offset-based pagination that conflicted with the max_batches limit
- Perform expensive SQL operations repeatedly

**Solution**: Completely rewrote the `run_incremental()` method in `DescriptionParser` with a simpler, more efficient approach:
- Load all jobs with descriptions once (eliminates repeated queries)
- Compute hashes in Python (more reliable than SQL-based hashing)
- Use set intersection for O(1) cache lookups
- Process in straightforward batches without complex windowing logic

**Performance Impact**: 
- Before: Hung indefinitely or took many minutes
- After: Completes in under 1 second for cache lookups, seconds for actual API calls
- Batch processing now works correctly with the configured limits

This fix ensures the description parser operates efficiently and respects the configured batch size and max batches settings.

### 2025-08-23 - Fixed Parsed Descriptions Data Integrity Issue

**Problem Resolved:** Fixed an issue where the database contained 1,048 parsed job descriptions for only 706 jobs, resulting in 342 orphaned descriptions. This occurred when jobs were purged from the `jobs` table but their corresponding parsed descriptions were not cleaned up.

**Changes Made:**
- Enhanced `get_job_summary()` in `src/utils/database.py` to include parsed descriptions statistics showing total entries, unique jobs with descriptions, and orphaned descriptions count
- Added `cleanup_orphaned_parsed_descriptions()` function to remove parsed descriptions that no longer have corresponding jobs
- Added new command-line mode `--mode cleanup-orphaned-descriptions` to clean up orphaned data
- Updated database summary display in `main.py` to show parsed descriptions overview with warnings when orphans are detected

**Usage:**
```bash
# Check for orphaned descriptions
python main.py --mode db-summary

# Clean up orphaned descriptions 
python main.py --mode cleanup-orphaned-descriptions
```

**Result:** Database now maintains data integrity with exactly 706 jobs and 706 parsed descriptions (1:1 ratio).
