# Use the official Microsoft Playwright image (includes all browser dependencies)
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Copy your python script into the container
COPY getMessagesText.py .

# Install required Python packages
RUN pip install --no-cache-dir playwright httpx
RUN playwright install chromium

# Run the bot
CMD ["python", "getMessagesText.py"]