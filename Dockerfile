FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir
RUN playwright install chromium

COPY getMessagesText.py .

CMD ["python", "-u", "xMessagesLogger.py"]
