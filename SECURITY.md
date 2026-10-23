# Security

## Data boundaries

- KnowLens listens on `127.0.0.1` by default and performs no telemetry or external reporting.
- Documents, indexes, conversations, and evolution rules stay in the local data directory (default `data/`).
- Uploaded content, questions, and images are sent to the **model endpoint you configure** for parsing, understanding, and answering. Follow your organization's data-compliance rules when choosing an endpoint.
- Keys are read only from environment variables or a local `.env` file; they are never written into code or committed.

## Reporting a vulnerability

Use GitHub **Security Advisories** for private reports. Do not open a public issue. Include reproduction steps, impact, and the affected version.

## Deployment guidance

- For LAN or server access, add authentication and TLS at the reverse proxy and restrict source networks.
- Never commit `.env`. If a key has ever entered version history, rotate it immediately.
- Back up `data/` regularly (SQLite and index directories); treat backups as sensitive data.