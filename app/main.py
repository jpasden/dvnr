import os
import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

from app.database import init_db
from app.routers import admin

# Verify API key is present at startup
_api_key = os.environ.get("ANTHROPIC_API_KEY")
if not _api_key:
    import warnings
    warnings.warn(
        "ANTHROPIC_API_KEY is not set. Add it to your .env file before parsing texts.",
        RuntimeWarning,
        stacklevel=1,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    yield
    # Shutdown — nothing to clean up


app = FastAPI(title="Dynamic Vocabulary Notes Reader", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(admin.router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/admin")
