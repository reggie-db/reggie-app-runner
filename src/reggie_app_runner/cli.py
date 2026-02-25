import argparse
import logging
import sys

from reggie_app_runner import runner


"""Command line entrypoint for reggie-app-runner."""


def main() -> int:
    parser = argparse.ArgumentParser(prog="reggie-app-runner")
    # Keep "run" accepted for compatibility, but default to executing directly.
    parser.add_argument("command", nargs="?", default=None)
    args = parser.parse_args()

    _configure_logging()
    if args.command in (None, "run"):
        runner.run()
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 1


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


if __name__ == "__main__":
    sys.exit(main())
