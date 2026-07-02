import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# === НАСТРОЙКИ ===
smtp_server = "mail.cmsilveiras.sp.gov.br"
smtp_port = 587
email = "controleinterno@cmsilveiras.sp.gov.br"
password = "cmsilveiras2024"

to_email = "jexoyeke@ozvmail.com"

# === ПИСЬМО ===
subject = "Тестовое письмо — Высокий приоритет"
body = """Привет!

Это тестовое письмо отправлено с **высоким приоритетом**.

"""

msg = MIMEMultipart()
msg['From'] = email
msg['To'] = to_email
msg['Subject'] = subject

# === ВЫСОКИЙ ПРИОРИТЕТ ===
msg['X-Priority'] = '1'          # 1 = Highest
msg['X-MSMail-Priority'] = 'High'
msg['Importance'] = 'high'

msg.attach(MIMEText(body, 'plain'))

# === ОТПРАВКА ===
try:
    server = smtplib.SMTP(smtp_server, smtp_port)
    server.starttls()
    server.login(email, password)
    server.send_message(msg)
    server.quit()
    
    print("✅ Письмо с ВЫСОКИМ приоритетом успешно отправлено на", to_email)
except Exception as e:
    print("❌ Ошибка:", e)