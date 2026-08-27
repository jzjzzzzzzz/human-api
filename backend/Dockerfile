FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
RUN pip install --no-cache-dir .
EXPOSE 8000
USER 65532:65532
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
