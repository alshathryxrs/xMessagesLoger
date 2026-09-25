# Official Playwright Python image — has Chromium + all deps pre-installed
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

# Install Playwright browsers
RUN playwright install chromium

COPY getMessagesText.py .

CMD ["python", "-u", "getMessagesText.py"]
