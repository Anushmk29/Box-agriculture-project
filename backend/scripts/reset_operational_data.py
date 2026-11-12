"""Retired operational reset entry point; importing it never connects to a database."""


def clear_operational_data():
    raise SystemExit(
        "Operational reset is retired; no data was changed. "
        "Use a reviewed maintenance procedure with explicit project selection, "
        "execution confirmation and a verified backup."
    )


if __name__ == "__main__":
    clear_operational_data()
