"""Allow ``python -m healthcheck`` to behave like the CLI module."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
