# Contributing to scpi-replay

Thanks for your interest! Bug reports, cassettes that expose a problem, and
pull requests are all welcome.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and asking for features

Open an [issue](https://github.com/steitec/scpi-replay/issues/new/choose) and
pick a template. For a bug, the command line and (if you can share it) the
cassette or the relevant lines of it help most.

Security problems do not belong in public issues; see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/steitec/scpi-replay.git
cd scpi-replay
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Running the tests

CI runs the same commands on Linux, Windows and macOS (Python 3.10 and 3.13):

```bash
pytest -v
pytest -v examples/test_with_cassette.py
```

The end-to-end tests start scpi-sim and talk to it with PyVISA-py. They bind
free ports only, so they can run in parallel with other things on your machine.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Keep the change focused; one topic per pull request.
3. Add or update tests for new behaviour and bug fixes.
4. Update `README.md` and `CHANGELOG.md` (an `## Unreleased` section at the top)
   when users will notice the change.
5. Make sure all tests pass, then open the pull request and fill in the
   template.

Style: follow the surrounding code. Plain Python, standard library only (the
package has no runtime dependencies), short docstrings, no type gymnastics.
The recorder must never change, reorder or noticeably delay forwarded bytes.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
