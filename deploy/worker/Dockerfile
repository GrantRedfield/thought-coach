# Thought Coach review worker: the whole pipeline (Stockfish, Whisper, the speech tagger) in one
# Linux image, so nobody has to install any of it. Build from the repository root:
#   docker build -f deploy/worker/Dockerfile -t thoughtcoach-worker .
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates zstd \
    && rm -rf /var/lib/apt/lists/*

# Stockfish 19 (the same version as the local setup), official Linux build, pinned.
RUN curl -fsSL -o /tmp/sf.tar.gz \
      https://github.com/official-stockfish/Stockfish/releases/download/sf_19/stockfish-linux-x86-64-universal.tar.gz \
    && mkdir /tmp/sf && tar -xzf /tmp/sf.tar.gz -C /tmp/sf \
    && find /tmp/sf -type f -name 'stockfish-linux-*' -exec mv {} /usr/local/bin/stockfish \; \
    && chmod +x /usr/local/bin/stockfish && rm -rf /tmp/sf.tar.gz /tmp/sf \
    && echo quit | /usr/local/bin/stockfish | head -1
ENV STOCKFISH_PATH=/usr/local/bin/stockfish

# The speech tagger: Ollama with the small model, pulled into the image.
RUN curl -fsSL https://ollama.com/install.sh | sh
RUN (ollama serve >/tmp/ollama.log 2>&1 &) && sleep 8 && ollama pull qwen2.5:3b

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3

# The speech-to-text model, downloaded once at build time.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small.en', device='cpu', compute_type='int8')"

COPY thoughtcoach ./thoughtcoach
COPY deploy/worker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV PYTHONIOENCODING=utf-8
ENTRYPOINT ["/entrypoint.sh"]
CMD ["--help"]
