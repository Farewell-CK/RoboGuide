"""Command-line entry point for the repository self-check."""

from .architecture import baseline_summary


def main() -> None:
    print(baseline_summary())


if __name__ == "__main__":
    main()
