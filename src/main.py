import asyncio
import subprocess
import sys
import httpx

# BUG FIX : les 3 agents sont maintenant tous démarrés
SERVERS = [
    ("target_searcher", "src.agents.api.target_api:app",    8001),
    ("scrapper",        "src.agents.api.scrapper_api:app",  8002),
    ("marketing",       "src.agents.api.marketing_api:app", 8003),  # ← ajouté
]


def start_servers() -> list:
    processes = []
    for name, module, port in SERVERS:
        p = subprocess.Popen([
            sys.executable, "-m", "uvicorn", module,
            "--host", "0.0.0.0",
            "--port", str(port),
            "--log-level", "warning",
        ])
        processes.append(p)
        print(f"  {name} démarré sur :{port}")
    return processes


async def wait_for_servers():
    print("Attente démarrage des serveurs...")
    for name, _, port in SERVERS:
        for _ in range(30):
            try:
                async with httpx.AsyncClient() as client:
                    await client.get(
                        f"http://localhost:{port}/health",
                        timeout=2.0
                    )
                print(f"  ✓ {name} prêt")
                break
            except Exception:
                await asyncio.sleep(1)
        else:
            print(f"  ✗ {name} n'a pas répondu dans les temps")


async def main():
    from src.graph.orchestrator import run_pipeline

    print("=== SBT Intelligence Pipeline ===\n")
    print("Démarrage des agents FastAPI...")
    processes = start_servers()
    await wait_for_servers()
    print("\nTous les agents sont prêts.\n")

    try:
        final_state = await run_pipeline(max_per_query=5, limit_scraping=20)

        if final_state.get("export_path"):
            print(f"\nExport CSV disponible : {final_state['export_path']}")
        if final_state.get("competitors_found", 0) > 0:
            print(f"Concurrents identifiés : {final_state['competitors_found']}")
    finally:
        print("\nArrêt des agents...")
        for p in processes:
            p.terminate()


if __name__ == "__main__":
    asyncio.run(main())
