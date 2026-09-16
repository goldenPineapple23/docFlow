from fastapi import FastAPI

from app.routers import admin, auth, documents, email_intake

app = FastAPI(title="DocFlow API")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(email_intake.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
