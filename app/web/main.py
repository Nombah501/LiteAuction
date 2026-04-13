from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from app.web.routers import include_all_routers

app = FastAPI(title="LiteAuction Admin", version="0.2.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


include_all_routers(app)


def main() -> None:
    uvicorn.run("app.web.main:app", host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
