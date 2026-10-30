# Examples

| File | What it is |
| --- | --- |
| `bench-dmm.scpi.jsonl` | A real recording: the session in `drive_dmm.py` against a simulated DMM (`scpi-sim serve dmm`), captured with `scpi-replay record`. |
| `drive_dmm.py` | The PyVISA-py session behind the cassette: `*IDN?`, `CONF:VOLT:DC 10`, three `READ?`, `SYST:ERR?`. |
| `rules.toml` | Tolerances for checking the cassette against a (noisy) DMM: ignore `*IDN?`, `READ?` within 0.05. |
| `test_with_cassette.py` | A pytest test that runs driver code against the cassette with the `scpi_cassette` fixture. |

Try it (needs `pip install scpi-replay scpi-sim pyvisa pyvisa-py`):

```bash
# 1. a simulated DMM on port 5025
scpi-sim serve dmm --port 5025 &

# 2. check the cassette against it: exit code 0
scpi-replay check bench-dmm.scpi.jsonl --target 127.0.0.1:5025 --rules rules.toml --timing fast

# 3. the same check against a DMM that reads 0.5 V too high: exit code 1
scpi-sim serve dmm --port 5026 --volts 5.5 &
scpi-replay check bench-dmm.scpi.jsonl --target 127.0.0.1:5026 --rules rules.toml --timing fast

# 4. use the cassette as the instrument
scpi-replay serve bench-dmm.scpi.jsonl --port 5027 &
python drive_dmm.py 5027

# 5. the pytest example
pytest test_with_cassette.py
```

Record the cassette again (the readings differ slightly each time because the
simulated DMM is noisy):

```bash
scpi-replay record --target 127.0.0.1:5025 --listen 0 --once -o bench-dmm.scpi.jsonl
python drive_dmm.py <port printed by the recorder>
```
