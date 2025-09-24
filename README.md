# Job Informer 🔍

An automated job monitoring and notification system that scrapes job postings, analyzes market trends with AI, and sends daily email reports.

## Features

- **Automated Job Scraping**: Monitors LinkedIn for new job postings.
- **Description Parser**: Incrementally parses job descriptions into structured JSON data for analysis.
- **Smart Data Management**: Avoids duplicate job notifications and stores historical data.
- **Customizable Email Reports**: Delivers daily summaries and market analysis to your inbox.
- **AI-Powered Job Purging**: Automatically remove unwanted job postings using LLM integration.

## CLI Usage

Run the scraper once with custom filters:

```bash
python main.py --run-once --keywords "Data Scientist" --locations "Berlin" --time week
```

`--time` accepts `day`, `week`, or `month` to limit results to jobs posted within that window (defaults to `day`).
