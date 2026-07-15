"""python3 -m oleum [--config oleum.toml] — run the MCP server on stdio."""
import argparse

from . import config
from .mcp_server import Server


def main():
    ap = argparse.ArgumentParser(prog="oleum")
    ap.add_argument("--config", default=None, help="path to oleum.toml")
    args = ap.parse_args()
    Server(config.load(args.config)).serve()


if __name__ == "__main__":
    main()
