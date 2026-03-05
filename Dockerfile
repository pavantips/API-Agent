# ─────────────────────────────────────────────────────────────────────────────
# Exam Scheduling Agent — Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# Build:  docker build -t exam-agent .
# Run:    docker run -it --env-file .env exam-agent
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Set working directory inside the container
WORKDIR /app

# ── Install dependencies first (cached layer — only rebuilds if requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy source code and config
COPY api_client.py .
COPY utils.py .
COPY store.py .
COPY main.py .
COPY chatbot.py .
COPY agent.py .
COPY config/ ./config/

# ── Create runtime directories
# These are gitignored but needed at runtime:
#   data/      → stores reservations.json (bookings)
#   responses/ → stores raw API response JSON files
RUN mkdir -p data responses

# ── Environment variables
# DO NOT bake real secrets into the image.
# Pass them at runtime via --env-file .env or -e flags.
# See .env.example for required variable names.
ENV PROCTORU_AUTH_TOKEN=""
ENV ANTHROPIC_API_KEY=""

# ── Default command: run the AI agent
# Override at runtime to run other entry points:
#   docker run -it --env-file .env exam-agent python3 chatbot.py
#   docker run -it --env-file .env exam-agent python3 main.py
CMD ["python3", "agent.py"]
