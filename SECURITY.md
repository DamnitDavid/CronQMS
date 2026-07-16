# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability in Proins, please report
it privately so it can be addressed before public disclosure.

- **Do not** open a public GitHub issue for security reports.
- Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
  on this repository (Security → Report a vulnerability), or contact the
  maintainer directly.
- Please include a description of the issue, steps to reproduce, affected
  version/commit, and impact.

You can expect an acknowledgement within a few business days. Once a report is
triaged we will keep you informed of remediation progress and coordinate a
disclosure timeline with you.

## Supported Versions

This project is pre-1.0 and under active development. Security fixes are applied
to the `main` branch. There is no long-term support commitment for older
commits or tags yet.

## Deployment Hardening

Proins ships secure-by-default, but a real deployment must configure a few
things — see the "Production checklist" in [README.md](README.md). In summary:

- Set strong, unique `SECRET_KEY` and `JWT_SECRET` values. The app **refuses to
  start** in a non-development `ENVIRONMENT` if these are left at the built-in
  placeholder values.
- Run with `ENVIRONMENT=production` and `DEBUG=false`. Session cookies are only
  marked `Secure` outside development, so production must be served over HTTPS.
- Use real database credentials (not the `docker-compose` development defaults).
- Public self-registration is disabled by default; leave it disabled unless you
  explicitly want open sign-ups.
