# Contributing to GuestDesk

Thank you for helping improve GuestDesk! This project exists to support
communities and vulnerable populations, so please keep that mission front of
mind when proposing changes.

## Getting Started

1. Fork the repository and create a topic branch.
2. Set up a virtualenv (`python3 -m venv .venv`), install requirements, and run
   the smoke tests (`python -m pytest`).
3. Follow the coding style already in the repository. Add docstrings or inline
   comments where behaviour is not obvious.
4. Update documentation (README, config notes, deployment steps) whenever you
   add or change functionality.

## Licensing of Contributions

By submitting a pull request you agree that your contributions are licensed
under the **GuestDesk Community License v1.1** (SPDX identifier
`LicenseRef-GDCL-1.1`) as described in `LICENSE`. If you include third-party
code or assets, confirm they are compatible with this license and include
attribution in source headers or a `NOTICE` entry as appropriate.

## Pull Request Checklist

- [ ] Code builds and automated tests pass (`python -m pytest`).
- [ ] New endpoints are protected with the appropriate roles/permissions.
- [ ] Sensitive features include rate limiting, CSRF protection, and logging as
      needed.
- [ ] Deployment docs or environment variable references are updated.
- [ ] You have @-mentioned maintainer(s) if the change is urgent or security
      sensitive.

Thanks again for supporting GuestDesk and the communities it serves ❤️
