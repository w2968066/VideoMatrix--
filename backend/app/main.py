import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router

# 确保工作目录正确，以便找到 ffmpeg
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

app = FastAPI(
    title="VideoMatrix API",
    description="VideoMatrix 短视频矩阵自动化混剪后端 API",
    version="2.0.0"
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
    return {"status": "ok"}


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

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
