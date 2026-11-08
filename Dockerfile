# scpi-replay in a container: a recorded SCPI cassette acts as the instrument on
# port 5025. By default the example cassette of a bench DMM; mount your own over
# /cassettes/cassette.scpi.jsonl.
#
#   docker run --rm -p 5025:5025 ghcr.io/steitec/scpi-replay
#   docker run --rm -p 5025:5025 -v ./dmm.scpi.jsonl:/cassettes/cassette.scpi.jsonl:ro ghcr.io/steitec/scpi-replay
FROM python:3.13-slim
LABEL org.opencontainers.image.source=https://github.com/steitec/scpi-replay \
      org.opencontainers.image.description="Replay recorded SCPI traffic as a stand-in instrument on a raw TCP socket" \
      org.opencontainers.image.licenses=MIT

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /src

COPY examples/bench-dmm.scpi.jsonl /cassettes/cassette.scpi.jsonl
RUN useradd --create-home --uid 10001 scpi
USER scpi
WORKDIR /cassettes

EXPOSE 5025
# Healthy once the port accepts connections. Replay state belongs to a connection, so
# this connect changes nothing for the clients under test.
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --start-interval=1s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 5025), 2).close()"]
ENTRYPOINT ["scpi-replay"]
CMD ["serve", "/cassettes/cassette.scpi.jsonl", "--host", "0.0.0.0"]
