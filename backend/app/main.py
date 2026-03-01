from fastapi import FastAPI
from app.config import get_settings
from api.routes.predictions import router as predictions_router
from fastapi.middleware.cors import CORSMiddleware

settings = get_settings()

app = FastAPI(
    title="Football Predictor API",
    description="ML-powered match predictions for Premier League and La Liga",
    version="0.1.0",
    debug=settings.debug
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(predictions_router, prefix="/api/v1", tags=["predictions"])

@app.get("/")
def root():
    return {
        "status": "ok",
        "environment": settings.environment,
        "leagues": [settings.premier_league_id, settings.la_liga_id]
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
