"""python -m server [--host 127.0.0.1] [--port 8000]"""
import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    from server.app import get_app
    uvicorn.run(get_app(), host=a.host, port=a.port, log_level="info", timeout_keep_alive=30)


if __name__ == "__main__":
    main()
