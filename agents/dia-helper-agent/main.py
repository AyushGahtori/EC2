"""Process launcher for dia-helper-agent on EC2."""

from server import app  # noqa: F401

if __name__ == "__main__":
    import server  # noqa

