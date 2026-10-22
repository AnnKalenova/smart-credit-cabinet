from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.enums import JobStatus
from app.models import PipelineJob

router = APIRouter(tags=["Служебное: здоровье и метрики"])

QUEUE_DEPTH = Gauge("pipeline_queue_depth", "Этапов конвейера в очереди", ["status"])


@router.get("/health/live", summary="Процесс жив")
def live():
    return {"status": "ok"}


@router.get("/health/ready", summary="Сервис готов: доступна БД")
def ready(response: Response, db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        response.status_code = 503
        return {"status": "unavailable", "db": "down"}
    return {"status": "ok", "db": "up"}


@router.get("/metrics", summary="Метрики Prometheus")
def metrics(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(PipelineJob.status, func.count()).group_by(PipelineJob.status)).all())
    for s in JobStatus:
        QUEUE_DEPTH.labels(status=s).set(counts.get(s, 0))
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
