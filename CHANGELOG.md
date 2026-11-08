# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## 0.1.1 - 2026-11-08

- Docker image `ghcr.io/steitec/scpi-replay`, built with every release: serves a mounted
  cassette (or the example DMM) on port 5025, unprivileged user, health check

## 0.1.0 - 2026-10-30

First release.

### Added

- `scpi-replay record`: transparent TCP proxy for a raw SCPI socket that writes
  a JSONL cassette (`scpi-replay/1`) while forwarding every byte unchanged.
  Several clients, binary blocks (definite and `#0`), `--once`.
- `scpi-replay serve`: replays a cassette as an instrument, in `lookup` or
  `strict` mode, with optional recorded timing and a summary on exit.
- `scpi-replay check`: replays the recorded commands against a target and
  compares the answers, with TOML rules (`value`, `ignore`, `exact`, `regex`,
  `length`), JUnit XML and JSON output.
- `scpi-replay show`: prints a cassette as a transcript.
- Python API (`Cassette`, `Recorder`, `ReplayServer`, `check`) and the pytest
  plugin with the `scpi_cassette` fixture and `--scpi-record`.
- `examples/`: a cassette recorded from a simulated DMM, rules, a pytest example.
