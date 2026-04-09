import asyncio
import json
import math
import re
from urllib.parse import urlparse

import anthropic
from loguru import logger

# CLIENT CLAUDE — singleton (instancié une seule fois, pas à chaque appel)
_claude_client: anthropic.Anthropic | None = None

def _get_claude() -> anthropic.Anthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _claude_client

from src.config import settings
from src.mcp.search_client import MCPSearchClient
from src.storage.database import init_db, save_search_result, get_known_domains
from src.storage.embeddings import generate_embedding
from src.storage.graph_store import GraphStore


# ============================================================
# MODE DE RECHERCHE
# ============================================================
# MODE RAPIDE (par défaut) : 14 requêtes totales (~30 secondes)
#   - 6 Tier 1 + 4 Tier 2 + 4 Competitors
#
# MODE COMPLET : 40 requêtes totales (~3-5 minutes)
#   - Remplacez TIER1_QUERIES par TIER1_QUERIES_FULL
#   - Remplacez TIER2_QUERIES par TIER2_QUERIES_FULL
#   - Remplacez COMPETITOR_QUERIES par COMPETITOR_QUERIES_FULL
# ============================================================


# ============================================================
# REQUÊTES — PROSPECTS (Tier 1 : fabricants coffrets)
# ============================================================

# MODE RAPIDE : 6 requêtes essentielles (décommentez TIER1_QUERIES_FULL pour mode complet)
TIER1_QUERIES = [
    '"coffret de comptage" fabricant France',
    '"coffret Enedis" fabricant OR constructeur',
    '"metering cabinet" manufacturer Europe OEM',
    '"quadro elettrico" produttore Italia',
    '"cuadro eléctrico" fabricante España',
    '"Schaltschrank" Hersteller Deutschland',
]

# MODE COMPLET : 19 requêtes (pour recherche exhaustive)
TIER1_QUERIES_FULL = [
    # France
    '"coffret de comptage" fabricant France',
    '"NF C 14-100" fabricant coffret electrique',
    '"coffret Enedis" fabricant OR constructeur',
    '"coffret de branchement electrique" fabricant site officiel',
    '"armoire de comptage" fabricant electrique France',
    '"metering cabinet" manufacturer Europe OEM',
    '"electrical meter enclosure" manufacturer',
    '"coffret de coupure" "coffret comptage" fabricant',
    'fabricant "coffret electrique" comptage consommation',
    # Italie
    '"quadro elettrico" produttore OR fabbricante Italia',
    '"armadio contatore" fabbricante elettrico',
    '"quadro di distribuzione" produttore OEM Italia',
    '"cablaggio industriale" "quadro elettrico" fornitore',
    # Espagne
    '"cuadro eléctrico" fabricante España',
    '"armario contador" fabricante electrico España',
    '"cuadro de distribución" fabricante industrial',
    # Allemagne
    '"Schaltschrank" Hersteller Deutschland',
    '"Zählerschrank" Hersteller OR Produzent',
    '"Elektroschrank" OEM Hersteller Europa',
]

# ============================================================
# REQUÊTES — PROSPECTS (Tier 2 : assembleurs / intégrateurs)
# ============================================================

# MODE RAPIDE : 4 requêtes essentielles
TIER2_QUERIES = [
    '"faisceau electrique" "coffret" sous-traitant OR assemblage',
    '"wiring harness" "meter cabinet" subcontractor',
    '"fascio elettrico" "quadro" terzista Italia',
    '"mazo de cables" subcontratista "cuadro eléctrico"',
]

# MODE COMPLET : 11 requêtes
TIER2_QUERIES_FULL = [
    '"faisceau electrique" "coffret" sous-traitant OR assemblage',
    '"cablage interne" coffret electrique assemblage sous-traitance',
    '"wiring harness" "meter cabinet" OR "metering enclosure" subcontractor',
    '"cable assembly" "electrical cabinet" manufacturer Europe',
    'sous-traitant cablage coffret Enedis faisceaux electriques',
    '"assemblage coffret" cablage electrique prestataire',
    '"panel wiring" subcontractor industrial harness Europe',
    '"cable harness" subcontractor Romania OR Bulgaria "electrical cabinet"',
    # Italie
    '"fascio elettrico" "quadro" terzista OR subfornitura Italia',
    '"assemblaggio quadro" cablaggio industriale fornitore',
    # Espagne
    '"mazo de cables" subcontratista "cuadro eléctrico"',
]

# ============================================================
# REQUÊTES — CONCURRENTS de SBT (câbleurs low-cost)
# ============================================================

# MODE RAPIDE : 4 requêtes essentielles
COMPETITOR_QUERIES = [
    '"câblage électrique" Maroc fabricant',
    '"wiring harness" Morocco manufacturer',
    '"câblage" Tunisie sous-traitant',
    '"cable assembly" Romania OR Bulgaria manufacturer',
]

# MODE COMPLET : 10 requêtes
COMPETITOR_QUERIES_FULL = [
    # Maroc
    '"câblage électrique" Maroc fabricant OR sous-traitant',
    '"faisceau électrique" Tanger OR Casablanca OR Kenitra',
    '"wiring harness" Morocco manufacturer automotive',
    '"cable assembly" Maroc fournisseur européen',
    # Tunisie (autres acteurs)
    '"câblage" Tunisie "coffret" OR "tableau électrique" sous-traitant',
    '"wiring harness" Tunisia manufacturer export Europe',
    # Roumanie / Bulgarie / Europe de l'Est
    '"câblage électrique" Roumanie sous-traitant',
    '"cable assembly" Romania OR Bulgaria manufacturer low-cost',
    '"faisceau électrique" "Europe de l\'Est" sous-traitant',
    '"wiring harness" Romania Bulgaria Poland subcontractor Europe',
]

EXCLUDED_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com",
    "youtube.com", "twitter.com", "x.com",
    "wikipedia.org", "studylibfr.com", "scribd.com",
    "amazon.com", "amazon.fr", "leroymerlin.fr",
    "cdiscount.com", "fnac.com", "darty.com",
    "manomano.fr", "domomat.com",
    "indeed.com", "glassdoor.com", "welcometothejungle.com",
    "usinenouvelle.com", "lelezard.com", "businesswire.com",
    "prnewswire.com", "lefigaro.fr", "lemonde.fr",
    "achatmat.com", "directindustry.fr", "directindustry.com", "hellopro.fr",
    "kompass.com", "europages.fr", "societe.com",
    "manageo.fr", "verif.com",
    "exportersindia.com", "indiamart.com", "alibaba.com",
    "globalinforesearch.com", "globenewswire.com",
    "forum-electricite.com", "guidelec.com",
    "construireenfrance.fr", "electriciteinfo.com",
    "batirmoinscher.com",
    "assurancedommageouvrage.org",
    "enedis.fr", "rte-france.com", "erdfdistribution.fr",
}

# ============================================================
# PROTOTYPES SÉMANTIQUES
# ============================================================

PROTOTYPES = {
    "tier_1": """
    entreprise qui vend des coffrets de comptage electrique,
    des armoires electriques, des tableaux electriques, des enclosures,
    des meter cabinets, des quadros elettrici, des produits finis de distribution electrique
    """,
    "tier_2": """
    entreprise qui fait du cablage electrique, des faisceaux electriques,
    du wiring harness, du cable assembly, du panel wiring,
    de l'assemblage electrique ou de l'integration industrielle
    """,
    "competitor": """
    entreprise de câblage électrique, faisceaux électriques, wiring harness,
    basée au Maroc, Tunisie, Roumanie, Bulgarie, Europe de l'Est,
    sous-traitant low-cost pour fabricants européens
    """,
}

PROTOTYPE_EMBEDDINGS = {}


# ============================================================
# HELPERS
# ============================================================

def extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_valid_url(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    domain = extract_domain(url)
    if not domain:
        return False
    for bad in EXCLUDED_DOMAINS:
        if bad in domain:
            return False
    if url.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".zip")):
        return False
    return True


def deduplicate(results: list[dict]) -> list[dict]:
    seen = {}
    for r in results:
        domain = r["domain"]
        if domain not in seen:
            seen[domain] = r
        else:
            if len(r.get("snippet", "")) > len(seen[domain].get("snippet", "")):
                seen[domain] = r
    return list(seen.values())


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_prototype_embeddings() -> None:
    global PROTOTYPE_EMBEDDINGS
    if PROTOTYPE_EMBEDDINGS:
        return
    logger.info("Génération des embeddings prototypes...")
    for label, text in PROTOTYPES.items():
        vec = generate_embedding(text.strip())
        if vec:
            PROTOTYPE_EMBEDDINGS[label] = vec
    logger.info(f"Prototypes chargés : {list(PROTOTYPE_EMBEDDINGS.keys())}")


def _extract_json_from_text(text: str) -> dict:
    """Extrait un objet JSON depuis une réponse texte Claude (supporte les objets imbriqués)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # regex corrigée : r'\{.*\}' avec DOTALL gère les objets imbriqués
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_by_embedding(title: str, snippet: str) -> dict:
    text = f"{title}. {snippet}".strip()
    vec  = generate_embedding(text)

    if not vec:
        return {"label": "unknown", "confidence": 0.0,
                "reason": "embedding non généré", "scores": {}}

    scores = {}
    for label, proto_vec in PROTOTYPE_EMBEDDINGS.items():
        if proto_vec:
            scores[label] = cosine_similarity(vec, proto_vec)

    if not scores:
        return {"label": "unknown", "confidence": 0.0,
                "reason": "aucun prototype disponible", "scores": {}}

    best_label  = max(scores, key=scores.get)
    best_score  = scores[best_label]

    return {
        "label":      best_label,
        "confidence": round(best_score, 3),
        "reason":     "classification par similarité sémantique",
        "scores":     scores,
    }


def classify_by_llm(title: str, snippet: str, domain: str = "") -> dict:
    """Classification via Claude Haiku (remplace Ollama)."""
    prompt = f"""
Tu classes des entreprises dans une supply chain électrique.
Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ou après.

Labels :
- tier_1     : fabricant ou vendeur de coffrets, armoires, tableaux électriques, meter cabinets
- tier_2     : sous-traitant câblage, faisceaux, wiring harness, assemblage industriel
- competitor : câbleur/faisceau low-cost basé au Maroc, Tunisie, Roumanie, Bulgarie, Europe de l'Est
- unknown    : information insuffisante ou hors secteur

Entreprise :
Titre   : {title}
Snippet : {snippet}
Domaine : {domain}

Format attendu :
{{"label": "tier_1", "confidence": 0.85, "reason": "explication courte"}}
"""
    try:
        message = _get_claude().messages.create(
            model=settings.claude_fast_model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        data = _extract_json_from_text(raw)
        return {
            "label":      data.get("label", "unknown"),
            "confidence": float(data.get("confidence", 0.0)),
            "reason":     data.get("reason", "classification LLM Claude"),
        }
    except Exception as e:
        return {"label": "unknown", "confidence": 0.0, "reason": f"erreur llm: {e}"}


def hybrid_classify(title: str, snippet: str, domain: str = "") -> dict:
    """
    1) Embeddings → pré-classification
    2) Claude Haiku → arbitrage si zone grise
    3) Rejet si confiance trop basse
    """
    emb_result = classify_by_embedding(title, snippet)

    if emb_result["confidence"] < 0.55:
        return {
            "label":      "unknown",
            "confidence": emb_result["confidence"],
            "reason":     "rejeté — score embedding trop bas",
        }

    if emb_result["confidence"] >= 0.72:
        return emb_result

    # Zone grise (0.55–0.72) → arbitrage Claude
    llm_result = classify_by_llm(title, snippet, domain)

    if llm_result["confidence"] >= 0.55:
        return llm_result

    return {
        "label":      emb_result["label"],
        "confidence": emb_result["confidence"],
        "reason":     "embedding confirmé (LLM non concluant)",
    }


def label_to_tier(label: str) -> int:
    return {"tier_1": 1, "tier_2": 2, "competitor": 3}.get(label, 0)


# ============================================================
# RECHERCHE VIA MCP
# ============================================================

async def search_and_collect(
    queries: list[str],
    mcp_client: MCPSearchClient,
    max_per_query: int = 10,
) -> list[dict]:
    all_results = []
    for query in queries:
        logger.info(f"[Serper/MCP] search: {query}")
        try:
            hits = await mcp_client.search(query, max_results=max_per_query)
            for r in hits:
                title   = r.get("title", "").strip()
                url     = r.get("url", "").strip()
                snippet = r.get("body", "").strip()
                if not is_valid_url(url):
                    continue
                domain = extract_domain(url)
                all_results.append({
                    "title": title, "url": url,
                    "snippet": snippet, "domain": domain, "query": query,
                })
        except Exception as e:
            logger.warning(f"Erreur search pour '{query}': {e}")
        await asyncio.sleep(settings.request_delay_seconds)
    return all_results


# ============================================================
# PIPELINE PRINCIPAL
# ============================================================

async def run_pipeline(max_per_query: int = 10) -> dict:
    logger.info("=== Target Searcher — Démarrage ===")

    init_db()
    build_prototype_embeddings()

    # Domaines déjà connus
    sqlite_domains = get_known_domains()
    try:
        with GraphStore() as gs:
            neo4j_domains = gs.get_known_domains()
    except Exception as e:
        logger.warning(f"Neo4j indisponible pour dédup: {e}")
        neo4j_domains = set()

    already_known = sqlite_domains | neo4j_domains
    logger.info(f"Domaines connus : {len(already_known)}")

    # Collecte brute : prospects + concurrents
    async with MCPSearchClient() as client:
        logger.info("Étape 1/4 — Recherche prospects (Tier 1 + Tier 2)")
        tier1_raw = await search_and_collect(TIER1_QUERIES, client, max_per_query)
        tier2_raw = await search_and_collect(TIER2_QUERIES, client, max_per_query)

        logger.info("Étape 2/4 — Recherche concurrents")
        comp_raw  = await search_and_collect(COMPETITOR_QUERIES, client, max_per_query)

    all_raw = tier1_raw + tier2_raw + comp_raw
    logger.info(f"{len(all_raw)} résultats bruts collectés")

    # Déduplication
    logger.info("Étape 3/4 — Déduplication")
    deduped = deduplicate(all_raw)
    deduped = [
        r for r in deduped
        if r["domain"].replace("www.", "") not in already_known
    ]
    logger.info(f"{len(deduped)} résultats après déduplication")

    # Classification hybride
    logger.info("Étape 4/4 — Classification hybride (Embeddings + Claude)")
    final_results = []
    competitors   = []

    for r in deduped:
        classification = hybrid_classify(r["title"], r["snippet"], r["domain"])
        label      = classification["label"]
        confidence = classification["confidence"]
        reason     = classification["reason"]
        tier       = label_to_tier(label)

        if tier == 0:
            logger.debug(f"Ignoré (unknown): {r['domain']}")
            continue

        score = int(confidence * 100)

        save_search_result(
            url=r["url"], domain=r["domain"],
            title=r["title"], snippet=r["snippet"],
            query=r["query"], tier_guess=tier,
            tier_final=tier, score=score,
            source="serper_mcp",
        )

        entry = {
            "title": r["title"], "url": r["url"],
            "domain": r["domain"], "query": r["query"],
            "label": label, "tier": tier,
            "confidence": confidence, "reason": reason, "score": score,
        }
        final_results.append(entry)

        if tier == 3:
            competitors.append(entry)

        logger.info(
            f"{'COMPETITOR' if tier==3 else f'Tier{tier}'} | "
            f"{r['domain']} | conf={confidence} | {reason}"
        )

    logger.success(f"{len(final_results)} résultats sauvegardés "
                   f"({len(competitors)} concurrents)")
    return {"results": final_results, "competitors": competitors}


# ============================================================
# MODE INTERACTIF
# ============================================================

async def main_async():
    print("=== Target Searcher (Serper + Embeddings + Claude) ===")
    n = input("Résultats par query [5] : ").strip()
    max_per_query = int(n) if n.isdigit() else 5

    data = await run_pipeline(max_per_query=max_per_query)
    results    = data["results"]
    competitors = data["competitors"]

    print(f"\n=== RÉSULTATS ({len(results)} total) ===")
    for i, r in enumerate(results[:20], 1):
        tag = "CONCUR" if r["tier"] == 3 else f"Tier{r['tier']}"
        print(f"{i:02d}. [{tag}] {r['domain']} | conf={r['confidence']} | {r['reason']}")

    print(f"\nConcurrents identifiés : {len(competitors)}")
    for c in competitors[:5]:
        print(f"  - {c['domain']} ({c['reason']})")


if __name__ == "__main__":
    asyncio.run(main_async())
