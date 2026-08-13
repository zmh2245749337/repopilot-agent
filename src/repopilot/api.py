"""Optional FastAPI interface for the same durable RepoPilot workflow.

Install with ``pip install -e '.[api]'`` before serving it with uvicorn.
"""
from __future__ import annotations

from pathlib import Path

from .core import RepoPilot


def create_app(repo: str | Path):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import StreamingResponse
        from pydantic import BaseModel
    except ImportError as error:  # pragma: no cover - depends on optional extra
        raise RuntimeError("Install the API extra: pip install -e '.[api]'") from error

    pilot = RepoPilot(Path(repo))
    app = FastAPI(title="RepoPilot", version="0.2.0")
    active: dict[str, object] = {}

    class TaskRequest(BaseModel):
        issue: str
        top_k: int = 5

    @app.post("/api/tasks")
    def create_task(request: TaskRequest):
        state = pilot.analyze(request.issue, request.top_k)
        pilot.propose_patch(state)
        active[state.task_id] = state
        return {"task_id": state.task_id, "status": state.status.value, "proposal": [item.__dict__ for item in state.proposal]}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        saved = pilot.tasks.get(task_id)
        if not saved:
            raise HTTPException(status_code=404, detail="task not found")
        return saved

    @app.post("/api/tasks/{task_id}/approve")
    def approve_task(task_id: str):
        state = active.get(task_id)
        if state is None:
            raise HTTPException(status_code=409, detail="task must be resumed in this server session before approval")
        result = pilot.apply_proposal(state)  # explicit API action is the human approval gate
        return result.__dict__

    @app.post("/api/tasks/{task_id}/verify")
    def verify_task(task_id: str, test_target: str = "tests"):
        state = active.get(task_id)
        if state is None or not getattr(state, "workspace", None):
            raise HTTPException(status_code=409, detail="approve the task in this server session before verification")
        test_result = pilot.run_pytest(test_target, root=Path(state.workspace))
        review = pilot.review_task(state, test_result)
        return {"test": test_result.__dict__, "review": review.__dict__, "report": pilot.report(state)}

    @app.get("/api/tasks/{task_id}/events")
    def task_events(task_id: str):
        saved = pilot.tasks.get(task_id)
        if not saved:
            raise HTTPException(status_code=404, detail="task not found")
        def stream():
            for event in saved["state"]["trace"]:
                yield f"event: {event['event']}\ndata: {event}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
