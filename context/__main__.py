"""Run the context tools on the local streamable HTTP endpoint."""

from context.server import build_server

if __name__ == "__main__":
    build_server().run(transport="streamable-http")
