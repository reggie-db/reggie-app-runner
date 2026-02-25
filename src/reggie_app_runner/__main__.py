"""Module entrypoint so users can run `python -m reggie_app_runner`."""

from reggie_app_runner.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
