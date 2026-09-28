"""HTTP endpoints for creating and inspecting agent runs."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.agent.loop import run_agent
from app.db.database import get_db
from app.db.models import Run
from app.schemas import RunCreate, RunCreated, RunDetail, RunSummary

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", response_model=RunCreated, status_code=202)
def create_run(body: RunCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Store the run, return its id immediately, and run the agent in the background."""
    if not config.GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.",
        )
    run = Run(goal=body.goal, status="pending")
    db.add(run)
    db.commit()
    background_tasks.add_task(run_agent, run.id)
    return RunCreated(id=run.id, status=run.status)


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


@router.get("", response_model=list[RunSummary])
def list_runs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return db.scalars(select(Run).order_by(Run.id.desc()).limit(limit)).all()
