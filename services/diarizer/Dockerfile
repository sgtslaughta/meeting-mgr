FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# omegaconf: unpickles pyannote/embedding's legacy pytorch-lightning checkpoint format.
RUN pip install --no-cache-dir "pyannote.audio>=3.1" fastapi "uvicorn[standard]" \
    "torch>=2.4" python-multipart omegaconf
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
