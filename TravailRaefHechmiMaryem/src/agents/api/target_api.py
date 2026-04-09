from fastapi import FastAPI
from pydantic import BaseModel
from src.agents.target_searcher import run_pipeline
from src.storage.database import get_pending_search_results

app = FastAPI(title="Target Searcher Agent", version="2.0")


class RunRequest(BaseModel):
    max_per_query: int = 5


class RunResponse(BaseModel):
    status:           str
    prospects_found:  int
    competitors_found: int
    message:          str


@app.get("/health")
def health():
    return {"status": "ok", "agent": "target_searcher"}


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest):
    try:
        data        = await run_pipeline(max_per_query=req.max_per_query)
        results     = data.get("results", [])
        competitors = data.get("competitors", [])
        prospects   = [r for r in results if r["tier"] in (1, 2)]
        return RunResponse(
            status="done",
            prospects_found=len(prospects),
            competitors_found=len(competitors),
            message=f"{len(prospects)} prospects + {len(competitors)} concurrents sauvegardés",
        )
    except Exception as e:
        return RunResponse(
            status="error",
            prospects_found=0,
            competitors_found=0,
            message=str(e),
        )


@app.get("/status")
def status():
    rows = get_pending_search_results(limit=1000)
    return {"pending": len(rows)}
