from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    status:              str
    prospects_found:     int
    prospects_scraped:   int
    competitors_found:   int    # nombre de concurrents identifiés (tier 3)
    marketing_insights:  dict
    report_path:         str    # chemin vers le rapport final
    export_path:         str    # chemin vers le CSV exporté
    messages:            Annotated[list, add_messages]
    errors:              list[str]
    max_per_query:       int
    limit_scraping:      int
