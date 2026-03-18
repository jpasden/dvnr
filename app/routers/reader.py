"""
Reader router — now only redirects / to /admin.

The parse and detect-language endpoints have been removed. Text parsing is
handled exclusively through the admin interface (app/routers/admin.py).
The public-facing reader is the static dist/reader.html file.
"""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/", include_in_schema=False)
async def home():
    return RedirectResponse(url="/admin")
