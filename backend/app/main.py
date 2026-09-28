import os
import sys
import json
import socket
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router

# 确保工作目录正确，以便找到 ffmpeg
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

app = FastAPI(
    title="VideoMatrix API",
    description="VideoMatrix 短视频矩阵自动化混剪后端 API",
    version="2.4.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version,
            "instance": os.environ.get("VIDEOMATRIX_INSTANCE", "")}


def main() -> None:
    """Entry point used by the PyInstaller binary.

    Accepts the same --host / --port arguments uvicorn does so the Electron
    main process can invoke the bundled exe identically across platforms.
    """
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(prog="videomatrix-backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    # Keep the socket open: port=0 is allocated atomically by the OS, with no
    # find-free-port/close/rebind race against another desktop application.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((args.host, args.port))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="info"))
    if os.environ.get("VIDEOMATRIX_INSTANCE"):
        print("VIDEOMATRIX_READY " + json.dumps({
            "port": listener.getsockname()[1], "version": app.version,
            "instance": os.environ["VIDEOMATRIX_INSTANCE"],
        }), flush=True)

        def watch_parent():
            # Electron owns stdin. EOF also handles an abnormal desktop exit.
            try:
                while sys.stdin.buffer.read(1):
                    pass
            finally:
                from .services.task_service import task_service
                task_service.stop_all_tasks()
                server.should_exit = True

        threading.Thread(target=watch_parent, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
