import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from mr_board.config import Settings
from mr_board.harvest import Harvester, Snapshot

log = logging.getLogger("uvicorn.error")
STATIC = Path(__file__).parent / "static"


@dataclass
class BoardResponse(Snapshot):
    error: str | None  # last harvest failure, while the previous snapshot is still served

    @classmethod
    def of(cls, snapshot: Snapshot, error: str | None) -> "BoardResponse":
        return cls(**{f.name: getattr(snapshot, f.name) for f in fields(snapshot)}, error=error)


@dataclass
class RefreshResponse:
    status: Literal["ok", "already running"]
    error: str | None = None


@dataclass
class HealthResponse:
    ready: bool
    error: str | None


class Board:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._harvester = Harvester(settings)
        self._data: Snapshot | None = None
        self._error: str | None = None
        self._lock = asyncio.Lock()
        self._last_attempt = 0.0

    async def refresh(self) -> None:
        async with self._lock:
            self._last_attempt = time.time()
            try:
                self._data = await self._harvester.harvest()
                self._error = None
                log.info(
                    "Harvested %d MRs in %ss",
                    len(self._data.mrs),
                    self._data.harvest_seconds,
                )
            except Exception as exc:  # keep serving the previous snapshot
                self._error = f"{type(exc).__name__}: {exc}"
                log.exception("Harvest failed")

    async def loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self._settings.refresh_minutes * 60)

    @property
    def data(self) -> Snapshot | None:
        return self._data

    @property
    def error(self) -> str | None:
        return self._error

    def is_locked(self) -> bool:
        return self._lock.locked()

    def elapsed_since_last_attempt(self) -> float:
        return time.time() - self._last_attempt


@asynccontextmanager
async def lifespan(app: FastAPI):
    board = Board(Settings.from_env())
    app.state.board = board
    task = asyncio.create_task(board.loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="MR Emoji Board", lifespan=lifespan)


@app.get("/api/board")
async def get_board() -> BoardResponse:
    board: Board = app.state.board
    if board.data is None:
        if board.error is not None:
            raise HTTPException(503, board.error)

        raise HTTPException(
            503,
            "First harvest still running, try again in a few seconds",
        )

    return BoardResponse.of(board.data, board.error)


@app.post("/api/refresh")
async def refresh() -> RefreshResponse:
    board: Board = app.state.board
    if board.is_locked():
        return RefreshResponse("already running")

    if board.elapsed_since_last_attempt() < 30:
        raise HTTPException(429, "Easy there, harvested less than 30s ago")
    await board.refresh()
    return RefreshResponse("ok", board.error)


@app.get("/api/health")
async def health() -> HealthResponse:
    board: Board = app.state.board
    return HealthResponse(ready=board.data is not None, error=board.error)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
