FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GASTOWN_HOST=0.0.0.0 \
    GASTOWN_PORT=8000

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
COPY gastown ./gastown

RUN python -m pip install --upgrade pip \
    && python -m pip install .

EXPOSE 8000

CMD ["gastown", "serve", "--host", "0.0.0.0", "--port", "8000"]
