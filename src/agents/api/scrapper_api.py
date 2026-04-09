from fastapi import FastAPI
from pydantic import BaseModel
from src.agents.scrapper_agent import main as scrapper_main
from src.storage.database import get_connection

app = FastAPI(title="Scrapper Agent", version="2.1")


class RunRequest(BaseModel):
    limit: int = 20


class RunResponse(BaseModel):
    status:  str
    scraped: int
    message: str


@app.get("/health")
def health():
    return {"status": "ok", "agent": "scrapper"}


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest):
    try:
        await scrapper_main(limit=req.limit)

        # BUG FIX : compter le vrai nombre de scraped depuis SQLite
        # (et non req.limit qui était retourné avant, peu importe ce qui s'est passé)
        with get_connection() as conn:
            scraped = conn.execute(
                "SELECT COUNT(*) FROM search_results WHERE status='scraped'"
            ).fetchone()[0]

        return RunResponse(
            status="done",
            scraped=scraped,
            message=f"{scraped} entreprises scrapées avec succès",
        )
    except Exception as e:
        return RunResponse(status="error", scraped=0, message=str(e))
