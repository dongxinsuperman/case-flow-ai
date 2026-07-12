# Security Policy

## Supported Versions

Case Flow is currently in early development. Security fixes target the `main`
branch.

## Reporting a Vulnerability

Please do not open a public issue for a vulnerability that includes exploit
details, credentials, private endpoints, or sensitive data.

Use GitHub's private vulnerability reporting feature if it is enabled for this
repository. If it is not enabled, contact the maintainers privately through the
repository owner's available GitHub contact path.

When reporting, include:

- A short description of the issue.
- Affected component or endpoint.
- Steps to reproduce, using dummy data where possible.
- Potential impact.
- Suggested mitigation, if known.

## Secret Handling

Never commit:

- `.env` files.
- API keys or model provider keys.
- Feishu plugin credentials.
- AI Phone internal endpoints that should not be public.
- Runtime report images or customer data.

The repository includes `.example` files for configuration shape only.

## Network Exposure

The built-in AI Phone integration assumes a trusted internal network and does
not send an auth token to AI Phone. Do not expose AI Phone endpoints or the
case-flow callback endpoint directly to the public internet without an
authenticating gateway, source allowlist, and access logging.
