import smtplib
import secrets
from email.utils import formataddr, parseaddr
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from datetime import datetime, timedelta

from config import Config


class EmailService:
    """Service for sending emails, particularly for password reset OTP codes."""
    SENDER_NAME = 'Aegis'

    @staticmethod
    def generate_otp(length: int = 6) -> str:
        """Generate a secure random OTP code."""
        return ''.join(secrets.choice('0123456789') for _ in range(length))

    @staticmethod
    def send_otp_email(recipient_email: str, otp_code: str) -> bool:
        """
        Send an OTP code to the recipient's email.
        Returns True if successful, False otherwise.
        """
        if not Config.MAIL_USERNAME or not Config.MAIL_PASSWORD:
            print("[EmailService] ERROR: Email credentials not configured in .env")
            return False

        try:
            # Create message
            message = MIMEMultipart('alternative')
            message['Subject'] = 'Aegis - Password Reset Code'
            _, sender_email = parseaddr(Config.MAIL_DEFAULT_SENDER)
            if not sender_email:
                sender_email = Config.MAIL_USERNAME

            message['From'] = formataddr((EmailService.SENDER_NAME, sender_email))
            message['To'] = recipient_email

            # Create HTML and plain text versions
            text_content = f"""
Aegis - Password Reset Request

Your password reset verification code is: {otp_code}

This code will expire in {Config.OTP_EXPIRY_MINUTES} minutes.

If you did not request a password reset, please ignore this email.

- Aegis Team
            """

            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            line-height: 1.6;
            color: #1E1B4B;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .container {{
            background: #5B4FE8;
            border-radius: 12px;
            padding: 30px;
            color: white;
        }}
        .content {{
            background: white;
            border-radius: 12px;
            padding: 30px;
            margin-top: 20px;
            color: #1E1B4B;
        }}
        .otp-code {{
            font-size: 32px;
            font-weight: 800;
            color: #5B4FE8;
            background: #EEEDFE;
            padding: 15px 30px;
            border-radius: 8px;
            text-align: center;
            letter-spacing: 8px;
            margin: 20px 0;
        }}
        .warning {{
            color: #6B6B8A;
            font-size: 13px;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 0.5px solid #D8D5F0;
        }}
        .header {{
            font-size: 28px;
            font-weight: 800;
            margin: 0;
            letter-spacing: -0.5px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1 class="header">Aegis</h1>
        <p>Password Reset Request</p>
    </div>
    <div class="content">
        <h2>Verify Your Identity</h2>
        <p>You have requested to reset your password. Use the verification code below to proceed:</p>

        <div class="otp-code">{otp_code}</div>

        <p><strong>This code will expire in {Config.OTP_EXPIRY_MINUTES} minutes.</strong></p>

        <div class="warning">
            <p>If you did not request a password reset, please ignore this email. Your account remains secure.</p>
            <p>For security reasons, never share this code with anyone.</p>
        </div>
    </div>
</body>
</html>
            """

            # Attach both versions
            part1 = MIMEText(text_content, 'plain')
            part2 = MIMEText(html_content, 'html')
            message.attach(part1)
            message.attach(part2)

            # Connect to SMTP server and send
            if Config.MAIL_USE_SSL:
                # Use SSL
                server = smtplib.SMTP_SSL(Config.MAIL_SERVER, Config.MAIL_PORT, timeout=10)
            else:
                # Use TLS
                server = smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT, timeout=10)
                if Config.MAIL_USE_TLS:
                    server.starttls()

            # Remove spaces from password (Gmail App Passwords often have spaces)
            password = Config.MAIL_PASSWORD.replace(' ', '')
            server.login(Config.MAIL_USERNAME, password)
            server.send_message(message)
            server.quit()

            print(f"[EmailService] OTP sent successfully to {recipient_email}")
            return True

        except smtplib.SMTPAuthenticationError:
            print("[EmailService] ERROR: Authentication failed. Check MAIL_USERNAME and MAIL_PASSWORD")
            return False
        except smtplib.SMTPException as e:
            print(f"[EmailService] SMTP ERROR: {e}")
            return False
        except Exception as e:
            print(f"[EmailService] ERROR: {e}")
            return False

    @staticmethod
    def calculate_expiry_time(minutes: int = None) -> str:
        """Calculate expiry time for OTP."""
        if minutes is None:
            minutes = Config.OTP_EXPIRY_MINUTES
        expiry = datetime.utcnow() + timedelta(minutes=minutes)
        return expiry.strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def is_expired(expires_at: str) -> bool:
        """Check if a timestamp has expired."""
        try:
            expiry_time = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S')
            return datetime.utcnow() > expiry_time
        except Exception:
            return True
