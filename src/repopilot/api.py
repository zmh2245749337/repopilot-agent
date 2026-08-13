"""Local FastAPI application for the evidence-grounded RepoPilot workflow."""

import json
from dataclasses import asdict
from pathlib import Path

from .chat import CodeRagAssistant, ConversationStore
from .core import RepoPilot, TaskState
from .tools import create_registry


def create_app(repo: str | Path):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, StreamingResponse
        from pydantic import BaseModel, Field
    except ImportError as error:  # pragma: no cover - depends on optional extra
        raise RuntimeError("Install the API extra: pip install -e '.[api]'") from error

    root = Path(repo).resolve()
    app = FastAPI(title="RepoPilot", description="Evidence-grounded repository engineering agent", version="0.3.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    active: dict[str, tuple[RepoPilot, TaskState]] = {}
    conversations = ConversationStore()
    rag_assistant = CodeRagAssistant(root, conversations=conversations)
    web_root = Path(__file__).parent / "web"

    class TaskRequest(BaseModel):
        issue: str = Field(min_length=3, max_length=8_000)
        top_k: int = Field(default=5, ge=1, le=20)
        test_target: str = Field(default="tests", min_length=1, max_length=300)

    class RejectionRequest(BaseModel):
        reason: str = Field(default="Rejected by human reviewer", max_length=1_000)

    class TestRequest(BaseModel):
        test_target: str = Field(default="tests", min_length=1, max_length=300)

    class ChatRequest(BaseModel):
        message: str = Field(min_length=2, max_length=8_000)
        conversation_id: str | None = Field(default=None, max_length=100)
        top_k: int = Field(default=4, ge=1, le=8)

    def task_view(pilot: RepoPilot, state: TaskState) -> dict:
        return {
            "task_id": state.task_id, "issue": state.issue, "status": state.status.value, "plan": state.plan,
            "proposal": [asdict(item) for item in state.proposal], "trace": state.trace,
            "review": asdict(state.review) if state.review else None,
            "workspace": state.workspace,
            "test_target": state.test_target,
            "baseline_test": state.baseline_test,
            "evidence": [asdict(pilot.evidence.items[item]) for item in state.evidence_ids],
        }

    def get_active(task_id: str) -> tuple[RepoPilot, TaskState]:
        if task_id in active:
            return active[task_id]
        pilot = RepoPilot(root)
        state = pilot.resume(task_id)
        if not state:
            raise HTTPException(status_code=404, detail="task not found")
        active[task_id] = (pilot, state)
        return pilot, state

    @app.get("/")
    def dashboard():
        return FileResponse(web_root / "index.html")

    @app.get("/health")
    def health():
        return {"status": "healthy", "repository": str(root), "mode": "local-only"}

    @app.get("/api/tools")
    def list_tools():
        registry = create_registry(RepoPilot(root))
        return {"tools": [{"name": tool.name, "risk": tool.risk} for tool in registry._tools.values()]}

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        """Read-only code RAG chat. It never creates or applies a patch."""
        return CodeRagAssistant.view(rag_assistant.ask(request.message, request.conversation_id, request.top_k))

    @app.post("/api/tasks")
    def create_task(request: TaskRequest):
        pilot = RepoPilot(root)
        state = pilot.analyze(request.issue, request.top_k)
        state.test_target = request.test_target
        pilot.tasks.save(state, pilot.evidence)
        active[state.task_id] = (pilot, state)
        return task_view(pilot, state)

    @app.post("/api/tasks/{task_id}/reproduce")
    def reproduce_task(task_id: str, request: TestRequest):
        pilot, state = get_active(task_id)
        result = pilot.reproduce(state, request.test_target)
        if result.summary != "tests_failed":
            raise HTTPException(status_code=409, detail="baseline test did not fail; no safe patch proposal will be created")
        pilot.propose_patch(state)
        return {"baseline": asdict(result), "task": task_view(pilot, state)}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        pilot, state = get_active(task_id)
        return task_view(pilot, state)

    @app.get("/api/tasks/{task_id}/events")
    def task_events(task_id: str):
        pilot, state = get_active(task_id)

        def stream():
            for event in state.trace:
                yield f"event: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/tasks/{task_id}/approve")
    def approve_task(task_id: str):
        pilot, state = get_active(task_id)
        result = pilot.apply_proposal(state)
        if not result.ok:
            raise HTTPException(status_code=409, detail=result.content)
        return {"result": asdict(result), "task": task_view(pilot, state), "diff": pilot.diff(state).content}

    @app.post("/api/tasks/{task_id}/reject")
    def reject_task(task_id: str, request: RejectionRequest):
        pilot, state = get_active(task_id)
        result = pilot.reject_proposal(state, request.reason)
        if not result.ok:
            raise HTTPException(status_code=409, detail=result.content)
        return {"result": asdict(result), "task": task_view(pilot, state)}

    @app.post("/api/tasks/{task_id}/verify")
    def verify_task(task_id: str, request: TestRequest):
        pilot, state = get_active(task_id)
        if not state.workspace:
            raise HTTPException(status_code=409, detail="approve the proposal before verification")
        test_result = pilot.run_pytest(request.test_target, root=Path(state.workspace), state=state)
        review = pilot.review_task(state, test_result)
        return {"test": asdict(test_result), "review": asdict(review), "task": task_view(pilot, state)}

    @app.get("/api/tasks/{task_id}/diff")
    def task_diff(task_id: str):
        pilot, state = get_active(task_id)
        result = pilot.diff(state)
        if not result.ok:
            raise HTTPException(status_code=409, detail=result.content)
        return {"diff": result.content}

    @app.get("/api/tasks/{task_id}/report")
    def task_report(task_id: str):
        pilot, state = get_active(task_id)
        return {"markdown": pilot.report(state)}

    return app
