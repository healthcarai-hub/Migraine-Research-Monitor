# One image for all three Railway services. The default command runs the API and web page; the two cron services override the
# start command with `research-agent job daily` and `research-agent job weekly`.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# pdftotext, used to read free PDF full texts; Pango and fonts, used by WeasyPrint to lay out the printed edition (PDF)
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-liberation fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY config ./config
COPY migrations ./migrations
# editable, so the config/ and migrations/ folders are found next to the code
RUN pip install -e ".[gmail,print]"

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
# Railway sets PORT; the shell is only there to read it
CMD ["sh", "-c", "exec research-agent api serve --host 0.0.0.0 --port ${PORT:-8000}"]
