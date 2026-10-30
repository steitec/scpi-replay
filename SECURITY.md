# Security Policy

## Supported versions

Only the latest release receives fixes.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | yes       |

## Scope

scpi-replay is a test tool. The recorder and the replay server accept
connections from anyone who can reach their port and have no authentication,
just like the instruments they stand in for. Cassettes contain everything that
was sent over the wire, including serial numbers and anything else the
instrument reports; check them before publishing. Reports about that design
are not vulnerabilities, but we are interested in issues such as:

- crashes or hangs of the recorder, server or checker caused by malformed
  traffic or a malformed cassette or rules file,
- code execution or file access through a cassette or rules file,
- the recorder altering, dropping or reordering forwarded bytes.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through
[GitHub's private vulnerability reporting](https://github.com/steitec/scpi-replay/security/advisories/new)
or by e-mail to hello@steitec.net.

Include the version, how to reproduce the problem and what impact you expect.
You will get an answer within a week. Once a fix is released, the report is
published as a GitHub security advisory, crediting you if you wish.
