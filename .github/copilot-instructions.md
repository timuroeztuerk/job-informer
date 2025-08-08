<!-- Use this file to provide workspace-specific custom instructions to Copilot. For more details, visit https://code.visualstudio.com/docs/copilot/copilot-customization#_use-a-githubcopilotinstructionsmd-file -->

# Job Informer Project Instructions

This is a Python project for automated job posting monitoring and notification system.

## Project Structure
- `src/scrapers/` - Web scraping modules for different job sites
- `src/scheduler/` - Task scheduling and automation
- `src/email_notifier/` - Email notification system
- `src/config/` - Configuration management
- `src/utils/` - Utility functions and helpers

## Key Technologies
- **Web Scraping**: Beautiful Soup, Selenium, Requests
- **Scheduling**: Python Schedule library
- **Email**: SMTP with secure authentication
- **Data Processing**: Pandas for job data manipulation
- **Logging**: Loguru for comprehensive logging

## Development Guidelines
- Use type hints for all function parameters and returns
- Implement proper error handling and logging
- Follow PEP 8 style guidelines
- Create modular, testable code
- Use environment variables for sensitive configuration
- Implement rate limiting for web scraping to be respectful to target sites

## Security Considerations
- Store sensitive data (emails, passwords, API keys) in environment variables
- Implement proper user-agent rotation for scraping
- Add delays between requests to avoid overwhelming target servers
