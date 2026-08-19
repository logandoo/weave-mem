"""stdio 入口：python -m app.mcp（宿主进程按 MCP 协议与客户端 stdin/stdout 通信）。"""
from app.mcp_server import build_mcp_server_from_config


def main() -> None:
    server = build_mcp_server_from_config()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
