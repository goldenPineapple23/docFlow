FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends redis-server && rm -rf /var/lib/apt/lists/*
CMD ["sh", "-c", "redis-server --bind :: --protected-mode no --port 6379 --daemonize yes && exec python3 -m http.server 8080 --bind ::"]
