from fastapi import FastAPI

from app.routers import admin, auth

app = FastAPI(title="DocFlow API")

app.include_router(auth.router)
app.include_router(admin.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
