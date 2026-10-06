# One small image for both the load generator and the CPU mock server (no GPU, no plotting).
#   docker build -f bench/Dockerfile -t llm-bench:dev .
# Default command runs the mock server; `python -m bench run ...` runs the load generator.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY bench/requirements.txt bench/requirements.txt
RUN pip install --require-hashes -r bench/requirements.txt

COPY bench/ bench/
# Recorded in every run's meta.json (the image has no git): docker build --build-arg GIT_COMMIT=...
ARG GIT_COMMIT=unknown
ENV BENCH_GIT_COMMIT=$GIT_COMMIT
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin bench \
    && mkdir /results && chown bench /results
USER 10001

EXPOSE 8000
ENTRYPOINT ["python", "-m", "bench"]
CMD ["mock", "--host", "0.0.0.0", "--port", "8000"]
