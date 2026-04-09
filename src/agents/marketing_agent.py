import csv
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import anthropic
from loguru import logger

from src.config import settings
from src.storage.graph_store import GraphStore

# CLIENT CLAUDE — singleton async (instancié une seule fois)
_claude_client: anthropic.AsyncAnthropic | None = None

def _get_claude() -> anthropic.AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _claude_client


# ============================================================
# PROMPTS
# ============================================================

_TIER1_PROMPT = """\
Tu es un expert en stratégie commerciale B2B industrielle.
Voici une liste d'entreprises fabricants de coffrets électriques (Tier 1).
SBT est une entreprise tunisienne spécialisée dans le câblage électrique
et les faisceaux. Elle cherche à devenir sous-traitant de ces fabricants.

Entreprises Tier 1 :
{companies}

Analyse chaque entreprise et retourne UNIQUEMENT un JSON valide :
{{
  "top_prospects": [
    {{
      "name": "...",
      "priority": "haute|moyenne|faible",
      "reason": "pourquoi SBT devrait les contacter",
      "contact_angle": "comment approcher cette entreprise"
    }}
  ],
  "summary": "résumé en 2 phrases"
}}

Critères de priorité :
- haute : grande entreprise, forte activité coffrets, présence internationale
- moyenne : entreprise moyenne, activité coffrets confirmée
- faible : petite entreprise ou activité incertaine
"""

_TIER2_PROMPT = """\
Tu es un expert en stratégie commerciale B2B industrielle.
Voici une liste d'entreprises intermédiaires (Tier 2) — assembleurs et
intégrateurs qui travaillent avec des fabricants de coffrets électriques.
SBT cherche à collaborer avec eux comme sous-traitant câblage.

Entreprises Tier 2 :
{companies}

Analyse chaque entreprise et retourne UNIQUEMENT un JSON valide :
{{
  "top_prospects": [
    {{
      "name": "...",
      "priority": "haute|moyenne|faible",
      "reason": "pourquoi SBT devrait les contacter",
      "contact_angle": "comment approcher cette entreprise"
    }}
  ],
  "summary": "résumé en 2 phrases"
}}
"""

_COMPETITOR_PROMPT = """\
Tu es un expert en intelligence compétitive industrielle.
SBT est une entreprise tunisienne spécialisée dans le câblage électrique.
Voici une liste de ses concurrents potentiels (câbleurs low-cost).

Concurrents identifiés :
{companies}

Analyse et retourne UNIQUEMENT un JSON valide :
{{
  "competitor_analysis": [
    {{
      "name": "...",
      "country": "...",
      "threat_level": "haute|moyenne|faible",
      "strengths": "points forts supposés",
      "sbt_advantage": "avantage de SBT face à ce concurrent"
    }}
  ],
  "competitive_summary": "positionnement compétitif de SBT en 2-3 phrases"
}}
"""

_PITCH_PROMPT = """\
Tu es un expert en prospection B2B industrielle.
SBT est une entreprise tunisienne spécialisée dans :
- Le câblage électrique industriel
- Les faisceaux électriques
- L'assemblage de coffrets de comptage
- La sous-traitance pour fabricants européens

Avantages compétitifs de SBT :
- Coûts de production 40-50% inférieurs à l'Europe
- Proximité géographique (Tunisie → France en 2h d'avion)
- Équipes qualifiées, normes européennes respectées (CE, RoHS)
- Flexibilité et réactivité sur les volumes

Voici une entreprise prospect :
Nom : {name}
Activité : {description}
Adresse : {address}
Email : {email}
Tier : {tier}
Pays cible : {country}

Génère un pitch commercial personnalisé et retourne UNIQUEMENT un JSON valide :
{{
  "subject": "objet d'email accrocheur et personnalisé (max 10 mots)",
  "pitch_email": "email de prospection complet (3-4 paragraphes, professionnel, personnalisé à cette entreprise)",
  "pitch_linkedin": "message LinkedIn court (3-4 phrases max, direct et engageant)",
  "key_argument": "l'argument principal adapté à cette entreprise spécifique",
  "follow_up": "suggestion de relance si pas de réponse"
}}

Règles :
- Personnalise chaque pitch avec le nom et l'activité de l'entreprise
- Mentionne un besoin concret que SBT peut combler pour cette entreprise
- Sois professionnel mais pas trop formel
- Écris dans la langue du pays cible (français pour France, italien pour Italie, etc.)
"""

_TARGETING_PROMPT = """\
Tu es un expert en développement commercial industriel.
SBT est une entreprise tunisienne de câblage électrique cherchant
des clients en Europe (France, Italie, Espagne, Allemagne principalement).

Voici les meilleurs prospects identifiés :

Tier 1 (fabricants coffrets) :
{tier1_prospects}

Tier 2 (assembleurs/intégrateurs) :
{tier2_prospects}

Génère un plan de ciblage et retourne UNIQUEMENT un JSON valide :
{{
  "priorité_1": {{
    "entreprises": ["nom1", "nom2"],
    "action": "action concrète à faire",
    "message_cle": "argument commercial principal"
  }},
  "priorité_2": {{
    "entreprises": ["nom3", "nom4"],
    "action": "action concrète à faire",
    "message_cle": "argument commercial principal"
  }},
  "priorité_3": {{
    "entreprises": ["nom5", "nom6"],
    "action": "action concrète à faire",
    "message_cle": "argument commercial principal"
  }},
  "conseil_global": "conseil stratégique en 2 phrases pour SBT"
}}
"""


# ============================================================
# APPEL LLM — Claude
# ============================================================

def _extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


async def _call_llm(prompt: str, smart: bool = False) -> dict:
    """
    Appel Claude via API Anthropic.
    smart=True → claude-sonnet (analyses marketing complexes)
    smart=False → claude-haiku (classification, extraction simple)
    """
    model = settings.claude_smart_model if smart else settings.claude_fast_model
    try:
        message = await _get_claude().messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_json(message.content[0].text)
    except json.JSONDecodeError:
        logger.warning("Claude : réponse non-JSON")
        return {}
    except Exception as e:
        logger.error(f"Erreur Claude : {e}")
        return {}


# ============================================================
# FORMATAGE
# ============================================================

def _format_companies(companies: list[dict]) -> str:
    """Inclut maintenant la description pour que Claude ait plus de contexte."""
    lines = []
    for c in companies:
        line = f"- {c.get('name', 'N/A')}"
        if c.get("description"):
            line += f" — {c['description'][:120]}"
        if c.get("email"):
            line += f" | email: {c['email']}"
        if c.get("address"):
            line += f" | adresse: {c['address']}"
        if c.get("confidence"):
            line += f" | score: {c['confidence']}"
        lines.append(line)
    return "\n".join(lines) if lines else "Aucune entreprise disponible"


def _detect_country(address: str | None, website: str | None = None) -> str:
    """
    Détection du pays en 2 étapes :
    1. Extension de domaine (fiable)   → .it → Italie, .de → Allemagne…
    2. Mots-clés dans l'adresse (fallback)
    """
    # 1. Par domaine
    if website:
        try:
            domain = urlparse(website).netloc.lower().replace("www.", "")
            tld_map = {".it": "Italie", ".de": "Allemagne", ".es": "Espagne",
                       ".fr": "France", ".ma": "Maroc", ".tn": "Tunisie",
                       ".ro": "Roumanie", ".bg": "Bulgarie", ".pl": "Pologne"}
            for tld, country in tld_map.items():
                if domain.endswith(tld):
                    return country
        except Exception:
            pass

    # 2. Par adresse
    if address:
        addr_lower = address.lower()
        if any(k in addr_lower for k in ["itali", "milano", "roma", "torino", "napoli", "venezia"]):
            return "Italie"
        if any(k in addr_lower for k in ["spain", "españa", "madrid", "barcelona", "espagne"]):
            return "Espagne"
        if any(k in addr_lower for k in ["deutsch", "germany", "münchen", "berlin", "hamburg", "allemagne"]):
            return "Allemagne"
        if any(k in addr_lower for k in ["maroc", "morocco", "casablanca", "tanger", "rabat", "kenitra"]):
            return "Maroc"
        if any(k in addr_lower for k in ["tunisie", "tunisia", "tunis", "sfax", "sousse"]):
            return "Tunisie"
        if any(k in addr_lower for k in ["romania", "roumanie", "bucharest", "cluj"]):
            return "Roumanie"

    return "France"


# ============================================================
# EXPORT CSV
# ============================================================

def _export_csv(pitches: list[dict], all_companies: dict, export_dir: str = "data/exports") -> str:
    """Génère un CSV exploitable par l'équipe commerciale."""
    Path(export_dir).mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y%m%d_%H%M")
    filepath  = f"{export_dir}/prospects_SBT_{date_str}.csv"

    fieldnames = [
        "priorité", "tier", "nom", "email", "téléphone",
        "site", "linkedin", "adresse", "pays",
        "objet_email", "argument_clé", "pitch_email", "pitch_linkedin", "relance",
    ]

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for pitch in pitches:
            name   = pitch.get("company", "")
            cdata  = all_companies.get(name, {})
            writer.writerow({
                "priorité":      pitch.get("priority", ""),
                "tier":          pitch.get("tier", ""),
                "nom":           name,
                "email":         cdata.get("email", ""),
                "téléphone":     cdata.get("phone", ""),
                "site":          cdata.get("website", ""),
                "linkedin":      cdata.get("linkedin", ""),
                "adresse":       cdata.get("address", ""),
                "pays":          _detect_country(cdata.get("address"), cdata.get("website")),
                "objet_email":   pitch.get("subject", ""),
                "argument_clé":  pitch.get("key_argument", ""),
                "pitch_email":   pitch.get("pitch_email", "").replace("\n", " "),
                "pitch_linkedin":pitch.get("pitch_linkedin", ""),
                "relance":       pitch.get("follow_up", ""),
            })

    logger.success(f"Export CSV : {filepath} ({len(pitches)} prospects)")
    return filepath


# ============================================================
# PIPELINE MARKETING
# ============================================================

async def run_marketing() -> dict:
    logger.info("=== Marketing Agent — Démarrage ===")

    with GraphStore() as gs:
        tier1 = gs.get_companies_by_tier(1)
        tier2 = gs.get_companies_by_tier(2)
        tier3 = gs.get_companies_by_tier(3)   # concurrents

    logger.info(f"Tier 1: {len(tier1)} | Tier 2: {len(tier2)} | Concurrents: {len(tier3)}")

    if not tier1 and not tier2:
        logger.warning("Aucune entreprise en base — pipeline marketing vide")
        return {
            "tier1_analysis":    {},
            "tier2_analysis":    {},
            "competitor_analysis": {},
            "targeting_plan":    {},
            "pitches":           [],
            "tier1_companies":   [],
            "tier2_companies":   [],
            "export_path":       "",
        }

    # 1. Analyse Tier 1 (Claude Sonnet — qualité)
    logger.info("Étape 1/5 — Analyse Tier 1")
    tier1_analysis = {}
    if tier1:
        tier1_analysis = await _call_llm(
            _TIER1_PROMPT.format(companies=_format_companies(tier1)),
            smart=True,
        )
        logger.info(f"Tier 1 — {len(tier1_analysis.get('top_prospects', []))} prospects")

    # 2. Analyse Tier 2 (Claude Sonnet)
    logger.info("Étape 2/5 — Analyse Tier 2")
    tier2_analysis = {}
    if tier2:
        tier2_analysis = await _call_llm(
            _TIER2_PROMPT.format(companies=_format_companies(tier2)),
            smart=True,
        )
        logger.info(f"Tier 2 — {len(tier2_analysis.get('top_prospects', []))} prospects")

    # 3. Analyse concurrents (Claude Haiku)
    logger.info("Étape 3/5 — Analyse concurrents")
    competitor_analysis = {}
    if tier3:
        competitor_analysis = await _call_llm(
            _COMPETITOR_PROMPT.format(companies=_format_companies(tier3)),
            smart=False,
        )
        logger.info(f"Concurrents analysés : {len(competitor_analysis.get('competitor_analysis', []))}")

    # 4. Plan de ciblage global (Claude Sonnet)
    logger.info("Étape 4/5 — Plan de ciblage")
    tier1_top = json.dumps(tier1_analysis.get("top_prospects", [])[:5], ensure_ascii=False)
    tier2_top = json.dumps(tier2_analysis.get("top_prospects", [])[:5], ensure_ascii=False)
    targeting_plan = {}
    if tier1_top or tier2_top:
        targeting_plan = await _call_llm(
            _TARGETING_PROMPT.format(tier1_prospects=tier1_top, tier2_prospects=tier2_top),
            smart=True,
        )

    # 5. Pitchs personnalisés (Claude Sonnet)
    logger.info("Étape 5/5 — Génération des pitchs")
    pitches       = []
    all_prospects = []
    for p in tier1_analysis.get("top_prospects", []):
        if p.get("priority") in ("haute", "moyenne"):
            all_prospects.append((p, 1))
    for p in tier2_analysis.get("top_prospects", []):
        if p.get("priority") in ("haute", "moyenne"):
            all_prospects.append((p, 2))

    all_companies = {c["name"]: c for c in tier1 + tier2}

    for prospect, tier in all_prospects[:10]:   # max 10 pitchs
        name         = prospect.get("name", "")
        company_data = all_companies.get(name, {})
        country      = _detect_country(company_data.get("address"), company_data.get("website"))
        prompt_pitch = _PITCH_PROMPT.format(
            name=name,
            description=company_data.get("description") or prospect.get("reason", "N/A"),
            address=company_data.get("address", "N/A"),
            email=company_data.get("email", "N/A"),
            tier=tier,
            country=country,
        )
        pitch_result = await _call_llm(prompt_pitch, smart=True)
        if pitch_result:
            pitch_result["company"]  = name
            pitch_result["tier"]     = tier
            pitch_result["priority"] = prospect.get("priority")
            pitches.append(pitch_result)
            logger.info(f"Pitch généré pour {name} ({country})")

    logger.info(f"{len(pitches)} pitchs générés")

    # 6. Export CSV
    export_path = ""
    if pitches:
        export_path = _export_csv(pitches, all_companies)

    insights = {
        "tier1_analysis":     tier1_analysis,
        "tier2_analysis":     tier2_analysis,
        "competitor_analysis": competitor_analysis,
        "targeting_plan":     targeting_plan,
        "pitches":            pitches,
        "tier1_companies":    tier1,
        "tier2_companies":    tier2,
        "competitors":        tier3,
        "export_path":        export_path,
    }

    logger.success("Marketing Agent terminé")
    return insights


# ============================================================
# MODE INTERACTIF
# ============================================================

async def main():
    import asyncio
    insights = await run_marketing()

    print("\n=== TIER 1 — TOP PROSPECTS ===")
    for p in insights.get("tier1_analysis", {}).get("top_prospects", []):
        print(f"  [{p.get('priority','?').upper()}] {p.get('name')}")
        print(f"    → {p.get('reason')}")

    print("\n=== TIER 2 — TOP PROSPECTS ===")
    for p in insights.get("tier2_analysis", {}).get("top_prospects", []):
        print(f"  [{p.get('priority','?').upper()}] {p.get('name')}")

    print("\n=== CONCURRENTS ===")
    for c in insights.get("competitor_analysis", {}).get("competitor_analysis", []):
        print(f"  [{c.get('threat_level','?').upper()}] {c.get('name')} ({c.get('country')})")
        print(f"    → Avantage SBT : {c.get('sbt_advantage')}")

    print("\n=== PLAN DE CIBLAGE ===")
    plan = insights.get("targeting_plan", {})
    for key in ["priorité_1", "priorité_2", "priorité_3"]:
        if key in plan:
            p = plan[key]
            print(f"\n  {key.upper()} : {p.get('entreprises')}")
            print(f"    Action : {p.get('action')}")
            print(f"    Message : {p.get('message_cle')}")

    if insights.get("export_path"):
        print(f"\n✅ Export CSV : {insights['export_path']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
