FROM python:3.11-slim

WORKDIR /app

# install deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code
COPY . .

# Railway provides $PORT at runtime; default to 8080 locally
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "gunicorn app:app --workers 2 --timeout 60 --bind 0.0.0.0:${PORT:-8080}"]
