import os
from openai import OpenAI
from dotenv import load_dotenv
import smtplib
from email.message import EmailMessage
load_dotenv()  

API_KEY = os.getenv("GROQ_API_KEY")
assert API_KEY, "ERROR: groq OpenAI Key is missing"



RESOURCE_ENDPOINT = "https://api.groq.com/openai/v1"
assert RESOURCE_ENDPOINT, "ERROR : groq url not responding"


class LLMService:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )

    def chat(self, prompt):
        response = self.client.chat.completions.create(
            model="openai/gpt-oss-safeguard-20b",   # 🔥 best model
            messages=[
                {"role": "system", "content": "you are an astronaut, you will give details of the space to me."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        return response.choices[0].message.content


class EmailService:
    def __init__(self):
        # Defaulting to Gmail's SMTP server
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", 587))
        self.email_address = os.getenv("EMAIL_ADDRESS")
        self.email_password = os.getenv("EMAIL_PASSWORD") # Note: Better to use an App Password for Gmail

    def send_email(self, to_email: str, subject: str, content: str):
        if not self.email_address or not self.email_password:
            raise ValueError("EMAIL_ADDRESS or EMAIL_PASSWORD environment variables are missing.")

        msg = EmailMessage()
        msg.set_content(content)
        msg['Subject'] = subject
        msg['From'] = self.email_address
        msg['To'] = to_email

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()  # Secure the connection
                server.login(self.email_address, self.email_password)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"Failed to send email: {e}")
            return False
