import httpx
from langgraph.graph import StateGraph, END
from src.state import AgentState

AGENTS = {
    "target":    "http://localhost:8001",
    "scrapper":  "http://localhost:8002",
    "marketing": "http://localhost:8003",
}


# ============================================================
# NOEUDS
# ============================================================

async def node_target_searcher(state: AgentState) -> AgentState:
    print("[Orchestrator] → target_searcher")
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{AGENTS['target']}/run",
                json={"max_per_query": state["max_per_query"]},
            )
            data = resp.json()

        # Le target_searcher retourne maintenant prospects + competitors
        prospects_found  = data.get("prospects_found", 0)
        competitors_found = data.get("competitors_found", 0)

        return {
            **state,
            "status":           "searching",
            "prospects_found":  prospects_found,
            "competitors_found": competitors_found,
            "messages": state["messages"] + [
                {"role": "target_searcher", "content": data.get("message", "")}
            ],
        }
    except Exception as e:
        return {
            **state,
            "errors": state["errors"] + [f"target_searcher: {e}"],
        }


async def node_scrapper(state: AgentState) -> AgentState:
    print("[Orchestrator] → scrapper_agent")
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(
                f"{AGENTS['scrapper']}/run",
                json={"limit": state["limit_scraping"]},
            )
            data = resp.json()
        return {
            **state,
            "status":            "scraping",
            "prospects_scraped": data.get("scraped", 0),
            "messages": state["messages"] + [
                {"role": "scrapper", "content": data.get("message", "")}
            ],
        }
    except Exception as e:
        return {
            **state,
            "errors": state["errors"] + [f"scrapper: {e}"],
        }


async def node_marketing(state: AgentState) -> AgentState:
    print("[Orchestrator] → marketing_agent")
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(f"{AGENTS['marketing']}/run")
            data = resp.json()

        insights    = data.get("insights", {})
        export_path = insights.get("export_path", "")

        return {
            **state,
            "status":            "done",
            "marketing_insights": insights,
            "export_path":       export_path,
            "messages": state["messages"] + [
                {"role": "marketing", "content": data.get("message", "")}
            ],
        }
    except Exception as e:
        return {
            **state,
            "errors": state["errors"] + [f"marketing: {e}"],
        }


# ============================================================
# CONDITIONS
# ============================================================

def should_scrape(state: AgentState) -> str:
    if state.get("prospects_found", 0) > 0:
        return "scrape"
    return "end"


def should_run_marketing(state: AgentState) -> str:
    if state.get("prospects_scraped", 0) > 0:
        return "marketing"
    return "end"


# ============================================================
# GRAPHE
# ============================================================

def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("target_searcher", node_target_searcher)
    graph.add_node("scrapper",        node_scrapper)
    graph.add_node("marketing",       node_marketing)

    graph.set_entry_point("target_searcher")

    graph.add_conditional_edges(
        "target_searcher",
        should_scrape,
        {"scrape": "scrapper", "end": END},
    )
    graph.add_conditional_edges(
        "scrapper",
        should_run_marketing,
        {"marketing": "marketing", "end": END},
    )
    graph.add_edge("marketing", END)

    return graph.compile()


async def run_pipeline(max_per_query: int = 5, limit_scraping: int = 20):
    graph = build_graph()

    initial_state: AgentState = {
        "status":             "starting",
        "prospects_found":    0,
        "prospects_scraped":  0,
        "competitors_found":  0,
        "marketing_insights": {},
        "report_path":        "",
        "export_path":        "",
        "messages":           [],
        "errors":             [],
        "max_per_query":      max_per_query,
        "limit_scraping":     limit_scraping,
    }

    final_state = await graph.ainvoke(initial_state)

    print("\n=== PIPELINE TERMINÉ ===")
    print(f"Statut              : {final_state['status']}")
    print(f"Prospects trouvés   : {final_state['prospects_found']}")
    print(f"Prospects scrapés   : {final_state['prospects_scraped']}")
    print(f"Concurrents détectés: {final_state['competitors_found']}")
    if final_state.get("export_path"):
        print(f"Export CSV          : {final_state['export_path']}")
    if final_state["errors"]:
        print(f"Erreurs             : {final_state['errors']}")

    return final_state
