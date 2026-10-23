# API + Linux workers (analyze / dedup / publish, and FFmpeg rendering).
# 剪映 me rendering needs macOS: run `python -m app.jobs.worker --queues render` on a Mac render node.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk fontconfig \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY mock_platforms ./mock_platforms
COPY scripts ./scripts
ENV DATA_DIR=/data HWACCEL=none RENDER_BACKEND=ffmpeg
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
