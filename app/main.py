from fastapi import FastAPI

app = FastAPI(
    title="Bodhrik Service Booking API",
    description="Service booking and review platform built for the Bodhrik Full Stack Assessment.",
    version="0.1.0",
)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}