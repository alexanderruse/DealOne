FROM python:3.12-slim-bookworm AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip==26.3 && pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 dealone
FROM dependencies AS browser-dependencies
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium && chmod -R a+rX /opt/playwright

FROM dependencies AS api
COPY . .
USER dealone
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM browser-dependencies AS worker
COPY . .
USER dealone
CMD ["python", "-m", "app.worker"]
