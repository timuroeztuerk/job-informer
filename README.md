# Job Informer 🔍

An automated job monitoring and notification system that scrapes job postings, analyzes market trends with AI, and sends daily email reports.

## Features

- **Automated Job Scraping**: Monitors LinkedIn for new job postings.
- **AI-Powered Market Analysis**: Uses Google Gemini to generate insights on job trends.
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
