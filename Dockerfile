FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY qia ./qia
COPY api ./api
USER 1000
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT}
