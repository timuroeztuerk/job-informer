"""
Email Notification Module
Handles sending email notifications with job reports
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pandas as pd
import html
from loguru import logger
from ..config.settings import Config


class EmailSender:
    """Handles email notifications for job updates"""
    
    def __init__(self, config: Config):
        self.config = config
        self.smtp_server = config.smtp_server
        self.smtp_port = config.smtp_port
        self.email_address = config.email_address
        self.email_password = config.email_password
        self.recipient_email = config.recipient_email
    
    def create_connection(self) -> smtplib.SMTP:
        """Create SMTP connection"""
        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.email_address, self.email_password)
            return server
        except Exception as e:
            logger.error(f"Failed to create SMTP connection: {e}")
            raise
    
    def create_job_report_html(self, jobs_df: pd.DataFrame) -> str:
        """Create HTML email template for job report"""
        from datetime import datetime
        
        # Prepare template data
        report_date = pd.Timestamp.now().strftime("%B %d, %Y at %I:%M %p")
        sources = ", ".join(jobs_df['source'].unique()) if not jobs_df.empty else "None"
        
        html_template = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width,initial-scale=1" />
          <title>Job Informer Report — {datetime.now().strftime('%b %d, %Y')}</title>
          <style>
            :root {{
              --bg: #0b0c10;
              --card: #121318;
              --card-2: #151722;
              --text: #e7e9ee;
              --muted: #a9afbd;
              --border: #252836;
              --accent: #4CAF50;
              --accent-2: #2196F3;
              --shadow: 0 10px 30px rgba(0,0,0,.35);
              --radius: 14px;
            }}
            @media (prefers-color-scheme: light) {{
              :root {{
                --bg: #f6f7fb;
                --card: #ffffff;
                --card-2: #ffffff;
                --text: #1d2330;
                --muted: #5b6473;
                --border: #e6e8ef;
                --accent: #4CAF50;
                --accent-2: #2196F3;
                --shadow: 0 10px 24px rgba(16,24,40,.08);
              }}
            }}
            * {{ box-sizing: border-box; }}
            html,body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Apple Color Emoji","Segoe UI Emoji"; }}
            .container {{ max-width: 980px; margin: 32px auto; padding: 0 20px; }}

            .header {{
              background: radial-gradient(1200px 500px at 10% -10%, rgba(76,175,80,.20), transparent 60%),
                          radial-gradient(900px 400px at 100% 0%, rgba(33,150,243,.12), transparent 60%),
                          linear-gradient(180deg, var(--card), var(--card-2));
              border: 1px solid var(--border);
              border-radius: var(--radius);
              padding: 28px 28px 24px;
              box-shadow: var(--shadow);
              position: relative;
              overflow: hidden;
            }}
            .header h1 {{ margin: 0 0 6px; font-size: 28px; letter-spacing: .3px; }}
            .subtle {{ color: var(--muted); font-size: 14px; margin: 0; }}

            .kpis {{
              display: grid;
              grid-template-columns: repeat(4, minmax(0,1fr));
              gap: 14px;
              margin-top: 18px;
            }}
            .kpi {{
              background: var(--card-2);
              border: 1px solid var(--border);
              border-radius: 12px;
              padding: 14px 16px;
            }}
            .kpi .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
            .kpi .value {{ margin-top: 6px; font-weight: 700; font-size: 18px; }}

            .panel {{
              margin-top: 22px;
              background: var(--card);
              border: 1px solid var(--border);
              border-radius: var(--radius);
              box-shadow: var(--shadow);
              overflow: hidden;
            }}
            .panel .panel-header {{
              display: flex; align-items: center; gap: 10px;
              padding: 16px 20px;
              border-bottom: 1px solid var(--border);
              background: linear-gradient(180deg, rgba(76,175,80,.10), transparent 70%);
            }}
            .chip {{
              display: inline-flex; align-items: center; gap: 8px;
              padding: 6px 10px;
              border: 1px solid var(--border);
              background: rgba(76,175,80,.10);
              border-radius: 999px;
              font-size: 12px; color: var(--accent);
              line-height: 1;
            }}
            .panel .panel-body {{ padding: 20px; }}

            .job-card {{
              background: var(--card-2);
              border: 1px solid var(--border);
              border-radius: 12px;
              padding: 18px;
              margin: 16px 0;
              transition: all 0.2s ease;
            }}
            .job-card:hover {{
              box-shadow: var(--shadow);
              transform: translateY(-2px);
            }}
            .job-title {{
              font-size: 18px;
              font-weight: 600;
              color: var(--accent-2);
              margin-bottom: 8px;
              line-height: 1.3;
            }}
            .job-title a {{
              color: var(--accent-2);
              text-decoration: none;
            }}
            .job-title a:hover {{
              text-decoration: underline;
            }}
            .job-details {{
              display: grid;
              grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
              gap: 8px;
              margin-top: 12px;
            }}
            .job-detail {{
              display: flex;
              align-items: center;
              gap: 8px;
              color: var(--muted);
              font-size: 14px;
            }}
            .job-detail .icon {{
              font-size: 16px;
            }}
            .job-salary {{
              color: var(--accent);
              font-weight: 600;
            }}
            .job-source {{
              display: inline-flex;
              align-items: center;
              gap: 6px;
              background: var(--accent-2);
              color: white;
              padding: 4px 8px;
              border-radius: 6px;
              font-size: 11px;
              text-transform: uppercase;
              letter-spacing: 0.5px;
              margin-top: 12px;
            }}

            .footer {{
              margin-top: 22px;
              padding: 16px 12px;
              color: var(--muted);
              text-align: center;
              font-size: 13px;
            }}

            /* Print */
            @media print {{
              .container {{ box-shadow: none; margin: 0; max-width: 100%; }}
              .header, .panel {{ break-inside: avoid; }}
              .kpis {{ grid-template-columns: repeat(4, 1fr); }}
              .footer {{ color: #666; }}
            }}

            /* Small screens */
            @media (max-width: 720px) {{
              .kpis {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
              .header h1 {{ font-size: 22px; }}
              .job-details {{ grid-template-columns: 1fr; }}
            }}
          </style>
        </head>
        <body>
          <div class="container">

            <section class="header">
              <h1>🔍 Job Informer Daily Report</h1>
              <p class="subtle">Generated on {report_date}</p>
              <div class="kpis">
                <div class="kpi">
                  <div class="label">Total Jobs</div>
                  <div class="value">{len(jobs_df)}</div>
                </div>
                <div class="kpi">
                  <div class="label">Sources</div>
                  <div class="value">{sources}</div>
                </div>
                <div class="kpi">
                  <div class="label">Keywords</div>
                  <div class="value">{self.config.search_keywords}</div>
                </div>
                <div class="kpi">
                  <div class="label">Locations</div>
                  <div class="value">{self.config.search_locations}</div>
                </div>
              </div>
            </section>

            <section class="panel">
              <div class="panel-header">
                <span class="chip">💼 Job Opportunities</span>
                <span class="muted">Latest job postings matching your criteria</span>
              </div>
              <div class="panel-body">
        """
        
        if not jobs_df.empty:
            for _, job in jobs_df.iterrows():
                job_url = job.get('url', '')
                safe_title = html.escape(str(job['title']))
                safe_company = html.escape(str(job['company']))
                safe_location = html.escape(str(job['location']))
                safe_salary = html.escape(str(job['salary']))
                safe_source = html.escape(str(job['source']))
                title_html = f'<a href="{html.escape(job_url)}" target="_blank">{safe_title}</a>' if job_url else safe_title
                
                html_template += f"""
                <div class="job-card">
                  <div class="job-title">{title_html}</div>
                  <div class="job-details">
                    <div class="job-detail">
                      <span class="icon">🏢</span>
                      <span>{safe_company}</span>
                    </div>
                    <div class="job-detail">
                      <span class="icon">📍</span>
                      <span>{safe_location}</span>
                    </div>
                    <div class="job-detail job-salary">
                      <span class="icon">💰</span>
                      <span>{safe_salary}</span>
                    </div>
                  </div>
                  <div class="job-source">{safe_source}</div>
                </div>
                """
        else:
            html_template += """
            <div class="job-card">
              <div class="job-title">No Jobs Found</div>
              <p style="color: var(--muted); margin: 8px 0 0 0;">No jobs were found matching your search criteria. Try adjusting your keywords or locations.</p>
            </div>
            """
        
        html_template += f"""
              </div>
            </section>

            <div class="footer">
              Generated by Job Informer · <span class="muted">This is an automated report. Happy job hunting!</span> 🚀
            </div>

          </div>
        </body>
        </html>
        """
        
        return html_template
    
    def send_job_report(self, jobs_df: pd.DataFrame, subject: str) -> bool:
        """Send job report email with HTML formatting"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = self.email_address
            msg['To'] = self.recipient_email
            msg['Subject'] = subject
            
            # Create HTML content
            html_content = self.create_job_report_html(jobs_df)
            html_part = MIMEText(html_content, 'html')
            
            # Create plain text alternative
            text_content = self.create_text_report(jobs_df)
            text_part = MIMEText(text_content, 'plain')
            
            # Attach both parts
            msg.attach(text_part)
            msg.attach(html_part)
            
            # Send email
            with self.create_connection() as server:
                server.send_message(msg)
            
            logger.debug(f"Job report email sent successfully to {self.recipient_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send job report email: {e}")
            return False
    
    def create_text_report(self, jobs_df: pd.DataFrame) -> str:
        """Create plain text version of job report"""
        text_lines = [
            "JOB INFORMER DAILY REPORT",
            "=" * 50,
            f"Report Date: {pd.Timestamp.now().strftime('%B %d, %Y at %I:%M %p')}",
            "",
            f"Total Jobs Found: {len(jobs_df)}",
            f"Keywords: {self.config.search_keywords}",
            f"Locations: {self.config.search_locations}",
            "",
            "JOB OPPORTUNITIES:",
            "-" * 30,
        ]
        
        if not jobs_df.empty:
            for _, job in jobs_df.iterrows():
                text_lines.extend([
                    f"Title: {str(job['title'])}",
                    f"Company: {str(job['company'])}",
                    f"Location: {str(job['location'])}",
                    f"Salary: {str(job['salary'])}",
                    f"Source: {str(job['source'])}",
                    f"URL: {str(job.get('url', 'N/A'))}",
                    "-" * 30,
                ])
        else:
            text_lines.append("No jobs found in this search.")
        
        text_lines.extend([
            "",
            "Happy job hunting!",
            "Generated by Job Informer"
        ])
        
        return "\n".join(text_lines)
    
    def send_error_notification(self, error_message: str) -> bool:
        """Send error notification email"""
        try:
            subject = "🚨 Job Informer Error Alert"
            
            # Create modern HTML template for error notification
            html_content = self._create_error_notification_html(error_message)
            
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['From'] = self.email_address
            msg['To'] = self.recipient_email
            msg['Subject'] = subject
            
            # Create plain text alternative
            body = f"""
            An error occurred while running Job Informer:
            
            Error Details:
            {error_message}
            
            Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}
            
            Please check the application logs for more details.
            
            Best regards,
            Job Informer System
            """
            
            # Attach both parts
            msg.attach(MIMEText(body, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))
            
            with self.create_connection() as server:
                server.send_message(msg)
            
            logger.debug("Error notification sent successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send error notification: {e}")
            return False
    
    def _create_error_notification_html(self, error_message: str) -> str:
        """Create HTML template for error notification"""
        from datetime import datetime
        
        html_template = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width,initial-scale=1" />
          <title>Job Informer Error Alert</title>
          <style>
            :root {{
              --bg: #0b0c10;
              --card: #121318;
              --card-2: #151722;
              --text: #e7e9ee;
              --muted: #a9afbd;
              --border: #252836;
              --accent: #ff4444;
              --accent-2: #ff6b6b;
              --shadow: 0 10px 30px rgba(0,0,0,.35);
              --radius: 14px;
            }}
            @media (prefers-color-scheme: light) {{
              :root {{
                --bg: #f6f7fb;
                --card: #ffffff;
                --card-2: #ffffff;
                --text: #1d2330;
                --muted: #5b6473;
                --border: #e6e8ef;
                --accent: #dc3545;
                --accent-2: #e74c3c;
                --shadow: 0 10px 24px rgba(16,24,40,.08);
              }}
            }}
            * {{ box-sizing: border-box; }}
            html,body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Apple Color Emoji","Segoe UI Emoji"; }}
            .container {{ max-width: 980px; margin: 32px auto; padding: 0 20px; }}

            .header {{
              background: radial-gradient(1200px 500px at 10% -10%, rgba(255,68,68,.20), transparent 60%),
                          radial-gradient(900px 400px at 100% 0%, rgba(255,107,107,.12), transparent 60%),
                          linear-gradient(180deg, var(--card), var(--card-2));
              border: 1px solid var(--border);
              border-radius: var(--radius);
              padding: 28px 28px 24px;
              box-shadow: var(--shadow);
              position: relative;
              overflow: hidden;
            }}
            .header h1 {{ margin: 0 0 6px; font-size: 28px; letter-spacing: .3px; color: var(--accent); }}
            .subtle {{ color: var(--muted); font-size: 14px; margin: 0; }}

            .panel {{
              margin-top: 22px;
              background: var(--card);
              border: 1px solid var(--border);
              border-radius: var(--radius);
              box-shadow: var(--shadow);
              overflow: hidden;
            }}
            .panel .panel-header {{
              display: flex; align-items: center; gap: 10px;
              padding: 16px 20px;
              border-bottom: 1px solid var(--border);
              background: linear-gradient(180deg, rgba(255,68,68,.10), transparent 70%);
            }}
            .chip {{
              display: inline-flex; align-items: center; gap: 8px;
              padding: 6px 10px;
              border: 1px solid var(--border);
              background: rgba(255,68,68,.10);
              border-radius: 999px;
              font-size: 12px; color: var(--accent);
              line-height: 1;
            }}
            .panel .panel-body {{ padding: 20px; }}

            .error-badge {{
              display: inline-flex;
              align-items: center;
              gap: 8px;
              background: rgba(255, 68, 68, 0.1);
              color: var(--accent);
              padding: 12px 16px;
              border-radius: 12px;
              border: 1px solid rgba(255, 68, 68, 0.2);
              font-weight: 600;
              margin: 16px 0;
            }}

            .error-details {{
              background: var(--card-2);
              border: 1px solid var(--border);
              border-radius: 12px;
              padding: 20px;
              font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
              font-size: 14px;
              line-height: 1.5;
              color: var(--text);
              white-space: pre-wrap;
              overflow-x: auto;
            }}

            .timestamp {{
              background: var(--card-2);
              border: 1px solid var(--border);
              border-radius: 12px;
              padding: 16px;
              margin-top: 16px;
              text-align: center;
            }}
            .timestamp-label {{
              color: var(--muted);
              font-size: 12px;
              text-transform: uppercase;
              letter-spacing: 0.08em;
              margin-bottom: 8px;
            }}
            .timestamp-value {{
              font-weight: 600;
              font-size: 16px;
              color: var(--text);
            }}

            .footer {{
              margin-top: 22px;
              padding: 16px 12px;
              color: var(--muted);
              text-align: center;
              font-size: 13px;
            }}

            @media (max-width: 720px) {{
              .header h1 {{ font-size: 22px; }}
            }}
          </style>
        </head>
        <body>
          <div class="container">

            <section class="header">
              <h1>🚨 Job Informer Error Alert</h1>
              <p class="subtle">System encountered an error during execution</p>
            </section>

            <div class="error-badge">
              <span>⚠️</span>
              <span>An error occurred while running Job Informer</span>
            </div>

            <section class="panel">
              <div class="panel-header">
                <span class="chip">🔍 Error Details</span>
                <span class="muted">Please review the information below</span>
              </div>
              <div class="panel-body">
                <div class="error-details">{error_message}</div>
                <div class="timestamp">
                  <div class="timestamp-label">Error Timestamp</div>
                  <div class="timestamp-value">{datetime.now().strftime('%B %d, %Y at %I:%M:%S %p')}</div>
                </div>
                <p style="margin-top: 20px; color: var(--muted); font-size: 14px;">
                  💡 <strong>Next Steps:</strong> Check the application logs for more detailed information about this error.
                </p>
              </div>
            </section>

            <div class="footer">
              Generated by Job Informer · <span class="muted">Automatic error monitoring system</span> 🤖
            </div>

          </div>
        </body>
        </html>
        """
        
        return html_template
    
    def send_test_email(self) -> bool:
        """Send test email to verify configuration"""
        try:
            subject = "✅ Job Informer Test Email"
            
            # Create modern HTML template for test email
            html_content = self._create_test_email_html()
            
            # Create email message
            msg = MIMEMultipart('alternative')
            msg['From'] = self.email_address
            msg['To'] = self.recipient_email
            msg['Subject'] = subject
            
            # Create plain text alternative
            body = f"""
            Hello!
            
            This is a test email from your Job Informer application.
            
            If you received this email, your email configuration is working correctly.
            
            Configuration Details:
            - SMTP Server: {self.smtp_server}
            - SMTP Port: {self.smtp_port}
            - From: {self.email_address}
            - To: {self.recipient_email}
            
            Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}
            
            Best regards,
            Job Informer System
            """
            
            # Attach both parts
            msg.attach(MIMEText(body, 'plain'))
            msg.attach(MIMEText(html_content, 'html'))
            
            with self.create_connection() as server:
                server.send_message(msg)
            
            logger.debug("Test email sent successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send test email: {e}")
            return False
    
    def _create_test_email_html(self) -> str:
        """Create HTML template for test email"""
        from datetime import datetime
        
        html_template = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width,initial-scale=1" />
          <title>Job Informer Test Email</title>
          <style>
            :root {{
              --bg: #0b0c10;
              --card: #121318;
              --card-2: #151722;
              --text: #e7e9ee;
              --muted: #a9afbd;
              --border: #252836;
              --accent: #4CAF50;
              --accent-2: #2196F3;
              --shadow: 0 10px 30px rgba(0,0,0,.35);
              --radius: 14px;
            }}
            @media (prefers-color-scheme: light) {{
              :root {{
                --bg: #f6f7fb;
                --card: #ffffff;
                --card-2: #ffffff;
                --text: #1d2330;
                --muted: #5b6473;
                --border: #e6e8ef;
                --accent: #4CAF50;
                --accent-2: #2196F3;
                --shadow: 0 10px 24px rgba(16,24,40,.08);
              }}
            }}
            * {{ box-sizing: border-box; }}
            html,body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Apple Color Emoji","Segoe UI Emoji"; }}
            .container {{ max-width: 980px; margin: 32px auto; padding: 0 20px; }}

            .header {{
              background: radial-gradient(1200px 500px at 10% -10%, rgba(76,175,80,.20), transparent 60%),
                          radial-gradient(900px 400px at 100% 0%, rgba(33,150,243,.12), transparent 60%),
                          linear-gradient(180deg, var(--card), var(--card-2));
              border: 1px solid var(--border);
              border-radius: var(--radius);
              padding: 28px 28px 24px;
              box-shadow: var(--shadow);
              position: relative;
              overflow: hidden;
            }}
            .header h1 {{ margin: 0 0 6px; font-size: 28px; letter-spacing: .3px; }}
            .subtle {{ color: var(--muted); font-size: 14px; margin: 0; }}

            .panel {{
              margin-top: 22px;
              background: var(--card);
              border: 1px solid var(--border);
              border-radius: var(--radius);
              box-shadow: var(--shadow);
              overflow: hidden;
            }}
            .panel .panel-header {{
              display: flex; align-items: center; gap: 10px;
              padding: 16px 20px;
              border-bottom: 1px solid var(--border);
              background: linear-gradient(180deg, rgba(76,175,80,.10), transparent 70%);
            }}
            .chip {{
              display: inline-flex; align-items: center; gap: 8px;
              padding: 6px 10px;
              border: 1px solid var(--border);
              background: rgba(76,175,80,.10);
              border-radius: 999px;
              font-size: 12px; color: var(--accent);
              line-height: 1;
            }}
            .panel .panel-body {{ padding: 20px; }}

            .config-grid {{
              display: grid;
              grid-template-columns: repeat(2, minmax(0, 1fr));
              gap: 16px;
              margin-top: 16px;
            }}
            .config-item {{
              background: var(--card-2);
              border: 1px solid var(--border);
              border-radius: 12px;
              padding: 16px;
            }}
            .config-label {{
              color: var(--muted);
              font-size: 12px;
              text-transform: uppercase;
              letter-spacing: 0.08em;
              margin-bottom: 8px;
            }}
            .config-value {{
              font-weight: 600;
              font-size: 14px;
              color: var(--text);
              word-break: break-all;
            }}

            .success-badge {{
              display: inline-flex;
              align-items: center;
              gap: 8px;
              background: rgba(76, 175, 80, 0.1);
              color: var(--accent);
              padding: 12px 16px;
              border-radius: 12px;
              border: 1px solid rgba(76, 175, 80, 0.2);
              font-weight: 600;
              margin: 16px 0;
            }}

            .footer {{
              margin-top: 22px;
              padding: 16px 12px;
              color: var(--muted);
              text-align: center;
              font-size: 13px;
            }}

            @media (max-width: 720px) {{
              .config-grid {{ grid-template-columns: 1fr; }}
              .header h1 {{ font-size: 22px; }}
            }}
          </style>
        </head>
        <body>
          <div class="container">

            <section class="header">
              <h1>✅ Job Informer Test Email</h1>
              <p class="subtle">Configuration verification successful</p>
            </section>

            <div class="success-badge">
              <span>🎉</span>
              <span>Your email configuration is working correctly!</span>
            </div>

            <section class="panel">
              <div class="panel-header">
                <span class="chip">⚙️ Configuration Details</span>
                <span class="muted">Current email settings</span>
              </div>
              <div class="panel-body">
                <div class="config-grid">
                  <div class="config-item">
                    <div class="config-label">SMTP Server</div>
                    <div class="config-value">{self.smtp_server}</div>
                  </div>
                  <div class="config-item">
                    <div class="config-label">SMTP Port</div>
                    <div class="config-value">{self.smtp_port}</div>
                  </div>
                  <div class="config-item">
                    <div class="config-label">From Address</div>
                    <div class="config-value">{self.email_address}</div>
                  </div>
                  <div class="config-item">
                    <div class="config-label">To Address</div>
                    <div class="config-value">{self.recipient_email}</div>
                  </div>
                </div>
                <div style="margin-top: 20px; padding: 16px; background: var(--card-2); border-radius: 12px;">
                  <div class="config-label">Test Timestamp</div>
                  <div class="config-value">{datetime.now().strftime('%B %d, %Y at %I:%M:%S %p')}</div>
                </div>
              </div>
            </section>

            <div class="footer">
              Generated by Job Informer · <span class="muted">Your system is ready to send job notifications!</span> 🚀
            </div>

          </div>
        </body>
        </html>
        """
        
        return html_template

    def _convert_markdown_to_html(self, text: str) -> str:
        """Convert basic markdown formatting to HTML"""
        import re
        
        # Handle headers
        text = re.sub(r'^### (.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
        text = re.sub(r'^## (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
        text = re.sub(r'^# (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
        
        # Handle bold text
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
        
        # Handle italic text
        text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
        text = re.sub(r'_(.+?)_', r'<em>\1</em>', text)
        
        # Handle bullet points
        lines = text.split('\n')
        in_list = False
        formatted_lines = []
        
        for line in lines:
            stripped_line = line.strip()
            
            # Check if this is a bullet point
            if stripped_line.startswith('- ') or stripped_line.startswith('• '):
                if not in_list:
                    formatted_lines.append('<ul>')
                    in_list = True
                # Remove the bullet and wrap in <li>
                content = stripped_line[2:].strip()
                formatted_lines.append(f'<li>{content}</li>')
            elif stripped_line.startswith('* '):
                if not in_list:
                    formatted_lines.append('<ul>')
                    in_list = True
                # Remove the asterisk and wrap in <li>
                content = stripped_line[2:].strip()
                formatted_lines.append(f'<li>{content}</li>')
            else:
                # If we were in a list and now we're not, close the list
                if in_list:
                    formatted_lines.append('</ul>')
                    in_list = False
                
                # Handle regular paragraphs
                if stripped_line:
                    formatted_lines.append(f'<p>{stripped_line}</p>')
                else:
                    formatted_lines.append('')
        
        # Close any open list
        if in_list:
            formatted_lines.append('</ul>')
        
        return '\n'.join(formatted_lines)