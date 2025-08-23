# AI-Purger Mode Documentation

## Overview

The AI-Purger mode uses Large Language Models (LLMs) to intelligently identify and remove unwanted job postings from the database. This is an advanced feature that complements the existing keyword-based filtering.

## Features

1. **Database Connection**: Connects to the existing SQLite database
2. **Job Analysis**: Retrieves job ID, title, and company information for all jobs
3. **Batch Processing**: Processes jobs in batches of 50 for efficiency and API limits
4. **LLM Analysis**: Sends job data to Gemini AI for intelligent analysis
5. **Safe Purging**: Removes only the jobs identified by the AI, with safety limits
6. **Comprehensive Logging**: Detailed logging of the entire process

## Usage

```bash
python main.py --mode ai-purge
```

## Configuration

### Required Environment Variables

- `GEMINI_API_KEY`: Your Google Gemini API key for LLM access

### Batch Processing

- **Batch Size**: 50 jobs per batch (configurable in the code)
- **API Delay**: 1-second delay between batches to respect API limits
- **Progress Tracking**: Detailed logging of each batch's progress

### PURGE_AI Prompt

Currently, the `PURGE_AI` prompt is empty and needs to be configured. You can modify it in the `AIPurger` class in `src/ai_job_purger/ai_purger.py`.

Example prompt structure (commented in the code):
```python
self.purge_prompt = """
You are an AI job filter. Analyze the provided job listings and identify jobs that should be purged.
Consider factors like:
- Duplicate or very similar positions
- Jobs from companies in unwanted list
- Jobs with titles containing unwanted keywords
- Low-quality or spam-like job postings

Return only the job IDs that should be purged as a JSON array.
"""
```

## Safety Features

1. **Empty Prompt Protection**: If the PURGE_AI prompt is empty, no jobs will be purged
2. **Percentage Limit**: The system will refuse to purge more than 50% of total jobs as a safety measure
3. **LLM Connection Test**: Tests the LLM connection before proceeding
4. **Detailed Logging**: Comprehensive logging for audit and debugging

## Process Flow

1. **Initialization**: Creates AIPurger instance and tests LLM connection
2. **Data Retrieval**: Fetches all jobs from the database (ID, title, company)
3. **Batch Division**: Splits jobs into batches of 50 for processing
4. **LLM Analysis**: Sends each batch to Gemini AI for analysis with 1-second delays
5. **Response Processing**: Parses JSON responses and validates job IDs per batch
6. **Batch Aggregation**: Combines results from all batches
7. **Safety Checks**: Validates the total response and applies safety limits
8. **Database Purging**: Removes the identified jobs from the database
9. **Summary Report**: Provides detailed summary including batch information

## Output

The mode provides a comprehensive summary:
- Jobs analyzed: Total number of jobs sent to the LLM
- Batches processed: Number of batches the jobs were divided into
- Jobs marked for purging: Number of jobs the LLM identified for removal
- Jobs actually purged: Number of jobs successfully removed from the database

Example output:
```
AI Purge Summary:
  - Jobs analyzed: 1170
  - Batches processed: 24
  - Jobs marked for purging: 45
  - Jobs actually purged: 45
```

## Error Handling

The system handles various error scenarios:
- Missing or invalid API key
- LLM connection failures
- Malformed LLM responses per batch
- Invalid job IDs returned by LLM (filtered out automatically)
- Database operation errors
- Safety limit violations
- Batch processing failures (individual batches can fail without stopping the entire process)

## Integration

The AI-Purger mode is fully integrated into the main application:
- Available via `--mode ai-purge` command line argument
- Requires Gemini API key configuration
- Uses existing logging and database infrastructure
- Follows the same patterns as other application modes
- Processes large datasets efficiently through batching

## Performance Considerations

- **Batch Size**: 50 jobs per batch provides good balance between API efficiency and processing speed
- **API Limits**: 1-second delay between batches respects API rate limits
- **Memory Usage**: Batching prevents memory issues with large datasets
- **Fault Tolerance**: Individual batch failures don't stop the entire process
- **Progress Tracking**: Real-time logging of batch progress

## Future Enhancements

1. **Configurable Batch Size**: Make batch size configurable via environment variables
2. **Configurable Prompts**: Move the PURGE_AI prompt to environment variables
3. **Multiple LLM Support**: Add support for other LLM providers
4. **Dry-Run Mode**: Preview what would be purged without actually removing jobs
5. **Whitelist Protection**: Protect certain jobs from being purged
6. **Resume Capability**: Resume processing from a specific batch if interrupted
7. **Parallel Processing**: Process multiple batches concurrently (with rate limiting)

## Example Usage Scenario

1. Run job scraping to collect jobs: `python main.py --mode run-once`
2. Review the database: `python main.py --mode db-summary`
3. Configure the PURGE_AI prompt in the code
4. Run AI purging: `python main.py --mode ai-purge`
   ```
   Processing 1170 jobs in 24 batches of 50
   Processing batch 1/24 (50 jobs)
   Batch 1 identified 2 jobs for purging
   Processing batch 2/24 (50 jobs)
   ...
   ```
5. Check results: `python main.py --mode db-summary`
