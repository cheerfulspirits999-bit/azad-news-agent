# Roman Urdu Translator API
# Build:   docker build -t roman-urdu-translator .
# Run:     docker run -d --name ru-translate -p 8080:8080 \
#            -e RU_PROVIDER=openai -e RU_MODEL=gpt-4o-mini \
#            -e RU_API_KEY=sk-... roman-urdu-translator
# Ya:      docker compose up -d
FROM python:3.12-slim

WORKDIR /app

# Koi third-party dependency nahi - sirf standard library
COPY roman_urdu.py http_server.py ./
COPY prompts/ ./prompts/
COPY tests/ ./tests/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    RU_PROVIDER=openai \
    RU_MODEL=gpt-4o-mini \
    RU_TEMPERATURE=0.2

EXPOSE 8080

# Container start hone se pehle offline tests (build-time sanity check)
RUN python3 tests/test_offline.py

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4).status==200 else 1)"

CMD ["python3", "http_server.py", "--host", "0.0.0.0", "--port", "8080"]
