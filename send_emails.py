#!/usr/bin/env python3
"""Sender e-poster til alle abonnenter"""

import json
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

with open('generated_reports.json', 'r') as f:
    reports = json.load(f)
print(f"Fant {len(reports)} rapporter")

if len(reports) == 0:
    print("Ingen rapporter!")
    exit(1)

smtp_server = "smtp.gmail.com"
smtp_port = 587
username = os.environ.get('EMAIL_USERNAME')
password = os.environ.get('EMAIL_PASSWORD')
gw_number = os.environ.get('GW_NUMBER', '?')
hours_until = os.environ.get('HOURS_UNTIL', '?')

subject = f"⚠️ HUSK Å OPPDATERE LAGET! GW{gw_number} - Kun {hours_until} timer igjen!"

print(f"Kobler til {smtp_server}...")
server = smtplib.SMTP(smtp_server, smtp_port)
server.starttls()
server.login(username, password)
print("Innlogget!")

for report in reports:
    try:
        email = report['email']
        name = report['name']
        report_file = report['report_file']
        
        print(f"Sender til {name} ({email})...")
        
        with open(report_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"FPL Analyzer Bot <{username}>"
        msg['To'] = email
        
        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)
        
        server.sendmail(username, email, msg.as_string())
        print(f"  Sendt!")
        
    except Exception as e:
        print(f"  Feil: {e}")

server.quit()
print(f"\nFerdig!")
