# Terminal recording: record, show, check passes, check fails.
# Needs vhs (https://github.com/charmbracelet/vhs), plus: pip install scpi-replay scpi-sim pyvisa pyvisa-py
# Run from the repository root:  vhs docs/demo.tape
Output docs/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1100
Set Height 640
Set Padding 16
Set TypingSpeed 40ms
Set Theme "Dracula"

Hide
Type "scpi-sim serve dmm --port 5025 > /dev/null 2>&1 &" Enter
Type "rm -f /tmp/demo.scpi.jsonl; clear" Enter
Show

Type "# Record a session through the proxy" Enter
Type "scpi-replay record --target 127.0.0.1:5025 --listen 5030 --once -o /tmp/demo.scpi.jsonl &" Enter
Sleep 1s
Type "python examples/drive_dmm.py 5030" Enter
Sleep 2s

Type "clear" Enter
Type "# Read it as a transcript" Enter
Type "scpi-replay show /tmp/demo.scpi.jsonl" Enter
Sleep 3s

Type "clear" Enter
Type "# Check the recording against the instrument" Enter
Type "scpi-replay check /tmp/demo.scpi.jsonl --target 127.0.0.1:5025 --rules examples/rules.toml --timing fast" Enter
Sleep 3s

Type "clear" Enter
Type "# Same check against a DMM with a 0.5 V offset" Enter
Type "scpi-sim serve dmm --port 5026 --volts 5.5 > /dev/null 2>&1 &" Enter
Sleep 1s
Type "scpi-replay check /tmp/demo.scpi.jsonl --target 127.0.0.1:5026 --rules examples/rules.toml --timing fast" Enter
Sleep 4s

Hide
Type "pkill -f \"scpi-sim serve\"; rm -f /tmp/demo.scpi.jsonl" Enter
