# Job Informer Dashboard

## Overview
Web-based dashboard for visualizing statistics from your parsed job descriptions. Shows insights on skills, tools, salaries, seniority levels, remote work options, and more.

## Features
- **Key Metrics**: Total jobs, average salary, most demanded programming language, remote work percentage
- **Interactive Charts**: Seniority levels, employment types, programming languages, tools & technologies
- **Data Tables**: Top skills and locations
- **Salary Analysis**: Statistics including average, median, and ranges
- **Experience Requirements**: Distribution of required years of experience
- **Auto-refresh**: Updates every 5 minutes

## Prerequisites
Install the required Python packages:

```bash
pip install flask plotly pandas
```

## Quick Start

1. **Start the dashboard**:
   ```bash
   python dashboard.py
   ```

2. **Open your browser** to:
   ```
   http://127.0.0.1:5000
   ```

3. **View your job market insights** in real-time!

## Command Line Options

```bash
# Custom port
python dashboard.py --port 8080

# Bind to all interfaces (accessible from network)
python dashboard.py --host 0.0.0.0

# Debug mode
python dashboard.py --debug

# Combined example
python dashboard.py --host 0.0.0.0 --port 8080 --debug
```

## Data Requirements

The dashboard requires:
- Jobs stored in your SQLite database (`data/jobs.db`)
- Parsed descriptions in the `parsed_descriptions` table (created by the description parser)

To ensure you have parsed data, run:
```bash
python main.py --mode parse-descriptions
```

## Dashboard Sections

### Key Metrics Cards
- **Total Jobs**: Number of jobs with parsed descriptions
- **Average Salary**: Mean salary from jobs with salary information
- **Top Language**: Most frequently mentioned programming language
- **Remote %**: Percentage of jobs offering remote or hybrid work

### Charts & Visualizations
- **Seniority Levels**: Distribution of job levels (intern, junior, mid, senior, etc.)
- **Employment Types**: Full-time, part-time, contract, internship breakdown
- **Remote Options**: Yes, no, hybrid work arrangements
- **Programming Languages**: Bar chart of most in-demand languages
- **Tools & Technologies**: Horizontal bar chart of popular tools
- **Experience Requirements**: Years of experience distribution

### Data Tables
- **Top Skills**: Most frequently mentioned skills with counts
- **Top Locations**: Most common job locations
- **Salary Statistics**: Detailed salary breakdown with count, average, median, and range

## Troubleshooting

### "No parsed job data found"
- Run the description parser: `python main.py --mode parse-descriptions`
- Check that you have jobs in your database: `python main.py --mode db-summary`

### Empty charts or missing data
- Some jobs may not have all fields parsed (e.g., salary, experience)
- Check the `desc_parser_prompt` in your config to ensure all desired fields are being extracted

### Connection errors
- Ensure the database file exists at `data/jobs.db`
- Check that the Flask dependencies are installed: `pip install flask plotly pandas`

## Customization

### Adding New Visualizations
Edit `dashboard.py` and add new methods to the `DashboardData` class, then update the HTML template and JavaScript accordingly.

### Styling Changes
The dashboard uses Bootstrap 5 and Chart.js. Modify `templates/dashboard.html` to customize the appearance.

### Data Filtering
You can modify the SQL query in `get_parsed_data()` to filter by date ranges, specific companies, or other criteria.

## API Endpoints

The dashboard exposes REST APIs:
- `GET /api/stats` - Raw statistics data as JSON
- `GET /api/charts` - Chart configuration data

These can be used to integrate with other tools or create custom visualizations.
