FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
RUN python -m py_compile bot/main.py bot/database.py bot/notion_client.py

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "bot.main"]
