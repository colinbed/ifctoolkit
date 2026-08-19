from fastapi import FastAPI

from app.api.health import router as health_router

app = FastAPI(title="Asset Information Assurance API", version="0.1.0")
app.include_router(health_router)
