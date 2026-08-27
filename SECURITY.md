# Security policy

Security fixes target the latest release. Report vulnerabilities through the repository's private **Security → Report a vulnerability** flow. Do not open a public issue containing an exploit, credential, prompt content, session cookie, raw API key, or personal data.

The implementation stores Argon2 password hashes, HMAC-SHA-256 API-key/session digests, and only a safe API-key prefix. Raw external keys are returned once. Browser mutations require both an HttpOnly session cookie and CSRF token. Authorization and ownership are checked server-side.

Operators remain responsible for TLS, edge DDoS controls, managed secrets, backups, patching, privacy disclosures, retention, account lifecycle, monitoring, and incident response.
