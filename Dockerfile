FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --uid 1001 --disabled-login app
USER app
ENV PORT=8080
EXPOSE 8080
HEALTHCHECK CMD curl -fs http://localhost:$PORT/sse || exit 1

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8080", "--debug", "--watch-dir", "watch_folder"]
