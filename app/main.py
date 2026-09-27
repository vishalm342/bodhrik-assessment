from fastapi import FastAPI

from app.api.auth import router as auth_router

app = FastAPI(
    title="Bodhrik Service Booking API",
    description="Service booking and review platform built for the Bodhrik Full Stack Assessment.",
    version="0.1.0",
)


app.include_router(auth_router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}
