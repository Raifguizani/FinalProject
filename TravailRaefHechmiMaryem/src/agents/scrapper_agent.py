import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

import anthropic
from loguru import logger

from src.config import settings
from src.models.company import Company
from src.mcp.search_client import MCPSearchClient
from src.storage.database import (
    get_pending_search_results,
    mark_search_result,
    save_raw_company,
)
from src.storage.embeddings import generate_embedding_async
from src.storage.graph_store import GraphStore

# ─────────────────────────────────────────────
# CLIENT CLAUDE — singleton async (instancié une seule fois)
# ─────────────────────────────────────────────
_claude_client: anthropic.AsyncAnthropic | None = None

def _get_claude() -> anthropic.AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _claude_client


_SUBPAGE_SLUGS = [
    "/contact", "/contact-us", "/contacts", "/contactez-nous",
    "/nous-contacter", "/about", "/about-us",
    "/a-propos", "/qui-sommes-nous",
    "/mentions-legales", "/mentions-légales", "/legal",
    "/contatti", "/chi-siamo",      # Italie
    "/contacto", "/quienes-somos", "/contactenos",  # Espagne
    "/kontakt", "/uber-uns", "/impressum",        # Allemagne
    "/footer", "/pied-de-page",
]
PRIORITY_SLUGS = [
    "/contact", "/nous-contacter", "/contact-us", "/contactez-nous",
    "/contatti", "/contacto", "/kontakt", "/contactenos",
]

_MD_LINK_RE    = re.compile(r"\[(?:[^\]]*)\]\((https?://[^)]+)\)")
_EMAIL_FALLBACK = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_FALLBACK = re.compile(
    r"(?<!\d)"
    r"(\+?(?:33|49|44|32|34|39|351|216|212|1)[\s.\-]?(?:\d[\s.\-]?){8,11}\d"
    r"|0\d[\s.\-]?(?:\d[\s.\-]?){7,8}\d)"
    r"(?!\d)"
)
_LINKEDIN_COMPANY_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/company/[a-zA-Z0-9\-]+/?",
    re.IGNORECASE
)

# Mapping TLD → Pays (étendu)
_COUNTRY_FROM_TLD = {
    ".fr": "France", ".it": "Italie", ".es": "Espagne", ".de": "Allemagne",
    ".ma": "Maroc", ".tn": "Tunisie", ".ro": "Roumanie", ".bg": "Bulgarie",
    ".be": "Belgique", ".ch": "Suisse", ".pt": "Portugal", ".pl": "Pologne",
    ".uk": "Royaume-Uni", ".co.uk": "Royaume-Uni", ".nl": "Pays-Bas",
    ".at": "Autriche", ".cz": "République Tchèque", ".hu": "Hongrie",
    ".sk": "Slovaquie", ".si": "Slovénie", ".hr": "Croatie",
    ".gr": "Grèce", ".se": "Suède", ".no": "Norvège", ".dk": "Danemark",
    ".fi": "Finlande", ".ie": "Irlande", ".lu": "Luxembourg",
    ".com": None, ".org": None, ".net": None, ".eu": None,
}

# Mots-clés pour détection du pays dans le contenu
_COUNTRY_KEYWORDS = {
    "France": ["france", "français", "française", "paris", "lyon", "marseille"],
    "Italie": ["italie", "italy", "italia", "italiano", "italiana", "rome", "milan", "milano"],
    "Espagne": ["espagne", "spain", "españa", "español", "española", "madrid", "barcelona"],
    "Allemagne": ["allemagne", "germany", "deutschland", "deutsch", "deutsche", "berlin", "munich", "münchen"],
    "Maroc": ["maroc", "morocco", "marocain", "marocaine", "casablanca", "rabat"],
    "Tunisie": ["tunisie", "tunisia", "tunisien", "tunisienne", "tunis"],
    "Roumanie": ["roumanie", "romania", "român", "română", "bucharest", "bucuresti"],
    "Bulgarie": ["bulgarie", "bulgaria", "bulgarian", "sofia"],
    "Belgique": ["belgique", "belgium", "belgië", "belgisch", "bruxelles", "brussels"],
    "Suisse": ["suisse", "switzerland", "schweiz", "svizzera", "genève", "geneva", "zürich"],
    "Portugal": ["portugal", "português", "portuguesa", "lisbon", "lisboa", "porto"],
    "Pologne": ["pologne", "poland", "polska", "polski", "warsaw", "warszawa"],
    "Royaume-Uni": ["royaume-uni", "united kingdom", "uk", "british", "london", "londres"],
    "Pays-Bas": ["pays-bas", "netherlands", "nederland", "dutch", "amsterdam"],
    "Autriche": ["autriche", "austria", "österreich", "vienna", "wien"],
}


def _detect_country(url: str, address: str | None, extracted_country: str | None, markdown: str = "") -> str | None:
    """Détecte le pays depuis: 1) LLM, 2) adresse, 3) contenu markdown, 4) TLD du domaine."""
    # Priorité 1: pays extrait par le LLM
    if extracted_country:
        return extracted_country
    
    # Priorité 2: pays dans l'adresse
    if address:
        addr_lower = address.lower()
        for country, keywords in _COUNTRY_KEYWORDS.items():
            if any(kw in addr_lower for kw in keywords):
                return country
    
    # Priorité 3: pays dans le contenu markdown (recherche dans les 2000 premiers caractères)
    if markdown:
        content_lower = markdown[:2000].lower()
        country_scores = {}
        for country, keywords in _COUNTRY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in content_lower)
            if score > 0:
                country_scores[country] = score
        
        if country_scores:
            # Retourner le pays avec le plus de mentions
            best_country = max(country_scores.items(), key=lambda x: x[1])
            if best_country[1] >= 2:  # Au moins 2 mentions pour être sûr
                return best_country[0]
    
    # Priorité 4: TLD du domaine
    try:
        domain = urlparse(url).netloc.lower()
        for tld, country in _COUNTRY_FROM_TLD.items():
            if domain.endswith(tld) and country:
                return country
    except Exception:
        pass
    
    return None


# ─────────────────────────────────────────────
# Extraction contacts avec fallback regex amélioré
# ─────────────────────────────────────────────

def _extract_contacts(mcp_result: dict, combined_markdown: str) -> tuple[str | None, str | None]:
    """Extrait email et téléphone avec priorité aux pages contact et validation stricte."""
    emails_mcp = mcp_result.get("emails", [])
    phones_mcp = mcp_result.get("phones", [])

    # Fallback regex si MCP n'a rien trouvé
    if not emails_mcp:
        emails_mcp = sorted(set(_EMAIL_FALLBACK.findall(combined_markdown)))
    if not phones_mcp:
        phones_mcp = sorted(set(_PHONE_FALLBACK.findall(combined_markdown)))

    def is_valid_email(e: str) -> bool:
        """Valide qu'un email est utilisable."""
        e_lower = e.lower()
        # Emails à rejeter complètement
        invalid_patterns = [
            "noreply", "no-reply", "donotreply", "example", "test",
            "webmaster", "postmaster", "admin@", "root@",
            "privacy@", "legal@", "abuse@", "support@",
            ".png", ".jpg", ".gif", ".css", ".js",  # Faux positifs
        ]
        if any(pattern in e_lower for pattern in invalid_patterns):
            return False
        
        # Vérifier que le domaine a au moins un point
        if "@" not in e or "." not in e.split("@")[1]:
            return False
        
        return True

    def email_score(e: str) -> int:
        """Score l'email pour prioriser les emails de contact."""
        e_lower = e.lower()
        
        # Emails génériques de contact (priorité haute)
        if any(g in e_lower for g in ["contact@", "info@", "sales@", "commercial@", "ventes@", "hello@"]):
            return 10
        
        # Emails de départements spécifiques (priorité moyenne)
        if any(g in e_lower for g in ["marketing@", "communication@", "rh@", "hr@"]):
            return 5
        
        # Emails personnels ou spécifiques (priorité basse mais valide)
        return 3

    def phone_score(p: str) -> int:
        """Score le téléphone pour prioriser les numéros complets."""
        # Enlever espaces/tirets pour compter les chiffres
        digits = re.sub(r'[^\d]', '', p)
        # Préférer les numéros avec indicatif international
        if p.startswith('+'):
            return len(digits) + 10
        return len(digits)

    # Filtrer et scorer les emails
    valid_emails = [e for e in emails_mcp if is_valid_email(e)]
    if valid_emails:
        valid_emails = sorted(valid_emails, key=email_score, reverse=True)
    
    if phones_mcp:
        phones_mcp = sorted(phones_mcp, key=phone_score, reverse=True)

    return (valid_emails[0] if valid_emails else None,
            phones_mcp[0] if phones_mcp else None)


# ─────────────────────────────────────────────
# Détection mentions
# ─────────────────────────────────────────────

def _detect_mentions(
    markdown: str,
    current_name: str,
    known_names: list[str],
    min_name_length: int = 5,
) -> list[str]:
    text_lower = markdown.lower()
    mentions   = []
    for name in known_names:
        if name == current_name or len(name) < min_name_length:
            continue
        if re.search(rf"\b{re.escape(name.lower())}\b", text_lower):
            mentions.append(name)
    return mentions


# ─────────────────────────────────────────────
# Sous-pages
# ─────────────────────────────────────────────

def _find_subpage_urls(base_url: str, markdown: str) -> list[str]:
    """Trouve les URLs de sous-pages pertinentes (contact, about, etc.)."""
    found = []
    seen  = set()
    
    # Chercher dans les liens du markdown
    for href in _MD_LINK_RE.findall(markdown):
        path = urlparse(href).path.lower().rstrip("/")
        for slug in _SUBPAGE_SLUGS:
            if slug in path:
                normalized = href.rstrip("/")
                if normalized not in seen:
                    seen.add(normalized)
                    found.append(href)
                break
    
    # Ajouter les URLs prioritaires construites
    base = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    for slug in PRIORITY_SLUGS:
        if len(found) >= 5:  # Augmenté de 3 à 5 pour plus de chances
            break
        candidate = urljoin(base, slug).rstrip("/")
        if candidate not in seen:
            seen.add(candidate)
            found.append(candidate)
    
    return found[:5]  # Augmenté de 3 à 5


async def _scrape_all_pages(client: MCPSearchClient, url: str, main_scrape: dict) -> str:
    main_md   = main_scrape.get("markdown", "")
    if not main_md:
        return ""
    markdowns = [f"# Page principale : {url}\n\n{main_md}"]
    for sub_url in _find_subpage_urls(url, main_md):
        try:
            page = await client.scrape(sub_url, max_chars=15000)
            if page.get("status") == "ok" and page.get("markdown"):
                markdowns.append(f"# Sous-page : {sub_url}\n\n{page['markdown']}")
        except Exception as e:
            logger.debug(f"  Sous-page ignorée {sub_url}: {e}")
    return "\n\n---\n\n".join(markdowns)[:60000]


# ─────────────────────────────────────────────
# Extraction LLM via Claude (singleton)
# ─────────────────────────────────────────────

_EXTRACT_PROMPT = """\
Extrais les informations suivantes depuis ce contenu web d'une entreprise.
Retourne UNIQUEMENT un objet JSON valide, sans texte avant ou après.

Format attendu (null ou [] si non trouvé) :
{{"name":"...","country":"...","address":"...","linkedin":"...","description":"...","partners":[]}}

Règles STRICTES :
- name : nom officiel court de l'entreprise (sans forme juridique si possible)
- country : pays EXACT où l'entreprise a son siège social (France, Italie, Espagne, Allemagne, Maroc, Tunisie, Roumanie, Bulgarie, Belgique, Suisse, Portugal, Pologne, Royaume-Uni, Pays-Bas, Autriche, etc.) ou null
- address : adresse complète du siège social (rue + code postal + ville + pays) en une seule chaîne ou null
- linkedin : URL COMPLETE et VALIDE de la page entreprise LinkedIn (format: "https://www.linkedin.com/company/nom-entreprise" ou "https://linkedin.com/company/nom-entreprise") ou null. NE PAS inventer, UNIQUEMENT si trouvé dans le contenu.
- description : 1-2 phrases sur l'activité principale (produits/services, secteur, marchés) ou null
- partners : liste de noms d'autres entreprises réelles mentionnées comme partenaires, clients ou fournisseurs

IMPORTANT pour LinkedIn :
- Chercher les liens vers linkedin.com/company/ dans le contenu
- Vérifier dans les sections "Suivez-nous", "Réseaux sociaux", "Contact", footer
- Si trouvé, copier l'URL EXACTE
- Si non trouvé, mettre null (NE PAS deviner)

Contenu :
{markdown}
"""


def _extract_linkedin_from_markdown(markdown: str) -> str | None:
    """Extrait l'URL LinkedIn depuis le markdown avec regex."""
    matches = _LINKEDIN_COMPANY_RE.findall(markdown)
    if matches:
        # Prendre le premier match et le nettoyer
        linkedin_url = matches[0].rstrip('/')
        # Normaliser vers https://www.linkedin.com
        linkedin_url = linkedin_url.replace("http://", "https://")
        if not linkedin_url.startswith("https://www."):
            linkedin_url = linkedin_url.replace("https://", "https://www.")
        return linkedin_url
    return None


async def _extract_company_llm(markdown: str, title: str) -> dict:
    """Extrait les infos entreprise via Claude avec détection du pays et LinkedIn."""
    sections = markdown.split("---")
    focused  = "\n---\n".join(s.strip()[:600] for s in sections if s.strip())[:3000]
    prompt   = _EXTRACT_PROMPT.format(markdown=focused)

    try:
        message  = await _get_claude().messages.create(
            model=settings.claude_fast_model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = message.content[0].text.strip()

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            data  = json.loads(match.group()) if match else {}

        # Normalisation de l'adresse
        address_raw = data.get("address")
        if isinstance(address_raw, dict):
            address_raw = ", ".join(str(v) for v in address_raw.values() if v)
        elif not isinstance(address_raw, str):
            address_raw = None

        # Validation et extraction LinkedIn (double vérification)
        linkedin_raw = data.get("linkedin")
        if linkedin_raw and "linkedin.com/company/" in str(linkedin_raw):
            # Nettoyer l'URL LinkedIn
            linkedin_raw = str(linkedin_raw).rstrip('/')
            linkedin_raw = linkedin_raw.replace("http://", "https://")
            if not linkedin_raw.startswith("https://www."):
                linkedin_raw = linkedin_raw.replace("https://", "https://www.")
        else:
            # Fallback: chercher dans le markdown complet avec regex
            linkedin_raw = _extract_linkedin_from_markdown(markdown)

        # Normalisation des partenaires
        partners_raw = data.get("partners") or []
        if not isinstance(partners_raw, list):
            partners_raw = []
        partners = [p for p in partners_raw if isinstance(p, str) and len(p) >= 5]

        # Extraction du pays
        country_raw = data.get("country")
        if country_raw and not isinstance(country_raw, str):
            country_raw = None

        return {
            "name":        data.get("name") or title,
            "country":     country_raw,
            "address":     address_raw,
            "linkedin":    linkedin_raw,
            "description": data.get("description"),
            "partners":    partners,
        }

    except Exception as e:
        logger.warning(f"LLM extraction échouée : {e}")
        # Même en cas d'erreur, essayer d'extraire LinkedIn avec regex
        linkedin_fallback = _extract_linkedin_from_markdown(markdown)
        return {"name": title, "country": None, "address": None, "linkedin": linkedin_fallback,
                "description": None, "partners": []}


# ─────────────────────────────────────────────
# Traitement d'une entreprise — thread-safe avec Lock
# ─────────────────────────────────────────────

async def process_company(
    row,
    client: MCPSearchClient,
    gs: GraphStore,
    known_names: set[str],
    known_names_lock: asyncio.Lock,
    tier1_companies: list[dict],
    tier2_companies: list[dict],
    semaphore: asyncio.Semaphore,
) -> bool:
    """Retourne True si succès, False sinon."""
    async with semaphore:
        url        = row["url"]
        domain     = row["domain"]
        title      = row["title"]
        snippet    = row["snippet"]
        tier_final = row["tier_final"]
        score      = row["score"]

        logger.info(f"[{domain}] Scraping...")

        main_scrape = await client.scrape(url, max_chars=20000)
        if not main_scrape.get("markdown"):
            logger.warning(f"[{domain}] Aucun contenu")
            mark_search_result(url, "error")
            return False

        combined_markdown = await _scrape_all_pages(client, url, main_scrape)
        final_email, final_phone = _extract_contacts(main_scrape, combined_markdown)
        extracted = await _extract_company_llm(combined_markdown, title)

        # Détection du pays avec fallback (inclut maintenant le markdown)
        final_country = _detect_country(
            url, 
            extracted.get("address"), 
            extracted.get("country"),
            combined_markdown
        )

        save_raw_company({
            "name":        extracted.get("name") or title,
            "phone":       final_phone or "",
            "email":       final_email or "",
            "website":     url,
            "description": extracted.get("description") or snippet,
            "address":     extracted.get("address") or "",
            "linkedin":    extracted.get("linkedin") or "",
            "raw":         combined_markdown[:5000],
        })

        company = Company(
            name=extracted.get("name") or title,
            website=url,
            country=final_country,
            tier=tier_final,
            email=final_email,
            phone=final_phone,
            address=extracted.get("address"),
            linkedin=extracted.get("linkedin"),
            description=extracted.get("description") or snippet,
            source="scrapper_agent",
            confidence=round(score / 100, 2) if score else None,
        )

        gs.upsert_company(company)
        gs.link_company_to_tier(company.name, company.tier)

        # ── Lock pour éviter les doublons sur known_names ──
        async with known_names_lock:
            known_names.add(company.name)
            current_known = list(known_names)

        # Relations — mentions
        mentions_found = _detect_mentions(combined_markdown, company.name, current_known)
        for mentioned in mentions_found:
            gs.create_mention_relation(company.name, mentioned)

        # Relations — partenaires LLM (UNIQUEMENT si déjà connus)
        # Ne pas créer de nouvelles entreprises pour éviter les doublons
        for partner in extracted.get("partners", []):
            if partner == company.name:
                continue
            async with known_names_lock:
                already_known = partner in known_names
            
            # Créer la relation UNIQUEMENT si le partenaire existe déjà
            if already_known:
                gs.create_mention_relation(company.name, partner)

        # POTENTIAL_SUPPLIER — top 10 par tier, sans contrainte géographique
        if company.tier == 2:
            top_t1 = sorted(tier1_companies,
                            key=lambda x: x.get("confidence") or 0, reverse=True)[:10]
            for t1 in top_t1:
                gs.create_potential_supplier(company.name, t1["name"],
                                             "Tier 2 câblage → Tier 1 fabricant coffrets")
        elif company.tier == 1:
            top_t2 = sorted(tier2_companies,
                            key=lambda x: x.get("confidence") or 0, reverse=True)[:10]
            for t2 in top_t2:
                gs.create_potential_supplier(t2["name"], company.name,
                                             "Tier 2 câblage → Tier 1 fabricant coffrets")

        embed_text = f"{company.name} {company.description or ''}"
        embedding  = await generate_embedding_async(embed_text)
        if embedding:
            gs.update_embedding(company.name, embedding)

        mark_search_result(url, "scraped")
        logger.success(f"[{domain}] ✓ Traitement terminé")
        return True


# ─────────────────────────────────────────────
# Pipeline principal — concurrent avec inspection des erreurs
# ─────────────────────────────────────────────

async def main(limit: int = 20):
    rows = get_pending_search_results(limit=limit)
    if not rows:
        logger.info("Aucune entreprise en attente.")
        return

    logger.info(f"{len(rows)} entreprise(s) | concurrence={settings.scraping_concurrency}")

    semaphore        = asyncio.Semaphore(settings.scraping_concurrency)
    known_names_lock = asyncio.Lock()

    with GraphStore() as gs:
        gs.create_constraints()
        known_names     = set(gs.get_company_names())
        tier1_companies = gs.get_companies_by_tier(1)
        tier2_companies = gs.get_companies_by_tier(2)

        async with MCPSearchClient() as client:
            tasks = [
                process_company(
                    row, client, gs,
                    known_names, known_names_lock,
                    tier1_companies, tier2_companies,
                    semaphore,
                )
                for row in rows
            ]
            # BUG FIX : return_exceptions=True + inspection explicite des erreurs
            results = await asyncio.gather(*tasks, return_exceptions=True)

        errors  = [r for r in results if isinstance(r, Exception)]
        success = sum(1 for r in results if r is True)

        if errors:
            logger.warning(f"{len(errors)} erreur(s) de scraping :")
            for e in errors[:5]:
                logger.warning(f"  ✗ {e}")

        logger.success(f"Scraping : {success}/{len(results)} réussis, {len(errors)} erreurs")

        gs.detect_and_upgrade_supplies()
        logger.success("Relations SUPPLIES détectées")


if __name__ == "__main__":
    asyncio.run(main())
