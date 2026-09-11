"""FastAPI 入口：健康检查 + LLM-first 预测接口。"""

from __future__ import annotations

from fastapi import FastAPI

from .models import PredictRequest, PredictResponse, Prediction
from .predictor import predict as run_predict

app = FastAPI(title="hcTools LLM-first Planner", version="0.1.0")


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/api/predict", response_model=PredictResponse)
async def predict(req: PredictRequest) -> PredictResponse:
    prediction: Prediction = await run_predict(req)
    return PredictResponse(tool=prediction.tool, params=prediction.params)
