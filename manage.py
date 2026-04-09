"""
Script de gestion du système SBT Intelligence.
Commandes: reset, scrape, marketing, dashboard
"""
import sys
import asyncio
import sqlite3
from pathlib import Path
from loguru import logger
from src.storage.graph_store import GraphStore

DB_PATH = Path("data/raw/staging.db")


def show_stats():
    """Affiche les statistiques du système."""
    logger.info("\n📊 STATISTIQUES DU SYSTÈME")
    logger.info("="*60)
    
    # SQLite
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute('SELECT COUNT(*) FROM search_results').fetchone()[0]
    pending = conn.execute('SELECT COUNT(*) FROM search_results WHERE status="pending"').fetchone()[0]
    scraped = conn.execute('SELECT COUNT(*) FROM search_results WHERE status="scraped"').fetchone()[0]
    error = conn.execute('SELECT COUNT(*) FROM search_results WHERE status="error"').fetchone()[0]
    raw_count = conn.execute('SELECT COUNT(*) FROM raw_company').fetchone()[0]
    
    logger.info(f"\n📁 SQLite (staging.db):")
    logger.info(f"   • Total: {total}")
    logger.info(f"   • Pending: {pending}")
    logger.info(f"   • Scraped: {scraped}")
    logger.info(f"   • Error: {error}")
    logger.info(f"   • Raw companies: {raw_count}")
    
    # Qualité des données
    if raw_count > 0:
        with_email = conn.execute('SELECT COUNT(*) FROM raw_company WHERE email != ""').fetchone()[0]
        with_phone = conn.execute('SELECT COUNT(*) FROM raw_company WHERE phone != ""').fetchone()[0]
        with_linkedin = conn.execute('SELECT COUNT(*) FROM raw_company WHERE linkedin != ""').fetchone()[0]
        
        logger.info(f"\n📈 Qualité des données:")
        logger.info(f"   • Avec email: {with_email}/{raw_count} ({with_email/raw_count*100:.0f}%)")
        logger.info(f"   • Avec téléphone: {with_phone}/{raw_count} ({with_phone/raw_count*100:.0f}%)")
        logger.info(f"   • Avec LinkedIn: {with_linkedin}/{raw_count} ({with_linkedin/raw_count*100:.0f}%)")
    
    conn.close()
    
    # Neo4j
    try:
        with GraphStore() as gs:
            with gs.driver.session() as session:
                result = session.run("MATCH (c:Company) RETURN count(c) as count")
                neo4j_count = result.single()["count"]
                
                logger.info(f"\n🔗 Neo4j (graphe):")
                logger.info(f"   • Entreprises: {neo4j_count}")
                
                # Par tier
                result = session.run("""
                    MATCH (c:Company)
                    WHERE c.tier IS NOT NULL
                    RETURN c.tier as tier, count(*) as count
                    ORDER BY tier
                """)
                records = list(result)
                if records:
                    logger.info(f"   • Répartition:")
                    for record in records:
                        logger.info(f"     - Tier {record['tier']}: {record['count']}")
    except Exception as e:
        logger.warning(f"   ⚠️  Neo4j non accessible: {e}")


def reset_system():
    """Réinitialise complètement le système."""
    logger.info("\n🔄 RÉINITIALISATION COMPLÈTE")
    logger.info("="*60)
    
    response = input("\n⚠️  Voulez-vous vraiment tout réinitialiser? (oui/non): ")
    if response.lower() not in ["oui", "yes", "y", "o"]:
        logger.info("❌ Annulé")
        return
    
    # SQLite
    conn = sqlite3.connect(DB_PATH)
    
    # Remettre en pending
    cursor = conn.execute('UPDATE search_results SET status = "pending"')
    logger.success(f"✓ {cursor.rowcount} search_results remis en pending")
    
    # Supprimer raw_company
    cursor = conn.execute('DELETE FROM raw_company')
    logger.success(f"✓ {cursor.rowcount} raw_company supprimées")
    
    conn.commit()
    conn.close()
    
    # Neo4j
    try:
        with GraphStore() as gs:
            with gs.driver.session() as session:
                result = session.run("MATCH (c:Company) RETURN count(c) as count")
                count = result.single()["count"]
                
                session.run("MATCH (c:Company) DETACH DELETE c")
                session.run("MATCH (t:Tier) DETACH DELETE t")
                
                logger.success(f"✓ {count} entreprises supprimées de Neo4j")
    except Exception as e:
        logger.warning(f"⚠️  Neo4j: {e}")
    
    logger.success("\n✅ Réinitialisation terminée")
    show_stats()


async def run_scraper(limit: int = 40):
    """Lance le scraper sur les entreprises en pending."""
    logger.info(f"\n🔍 SCRAPING ({limit} entreprises max)")
    logger.info("="*60)
    
    from src.agents.scrapper_agent import main as scrapper_main
    await scrapper_main(limit=limit)
    
    logger.success("\n✅ Scraping terminé")
    show_stats()


async def run_marketing():
    """Lance l'agent marketing."""
    logger.info("\n🎯 AGENT MARKETING")
    logger.info("="*60)
    
    from src.agents.marketing_agent import main as marketing_main
    result = await marketing_main()
    
    if result and result.get("export_path"):
        logger.success(f"\n✅ Export généré: {result['export_path']}")
    
    logger.success("\n✅ Marketing terminé")


def run_dashboard():
    """Lance le dashboard web."""
    logger.info("\n🌐 DASHBOARD WEB")
    logger.info("="*60)
    logger.info("\nDémarrage sur http://localhost:5000")
    logger.info("Appuyez sur Ctrl+C pour arrêter\n")
    
    from src.web.app import app
    app.run(debug=False, port=5000)


def show_help():
    """Affiche l'aide."""
    print("""
╔════════════════════════════════════════════════════════════════╗
║              SBT INTELLIGENCE - GESTION                        ║
╚════════════════════════════════════════════════════════════════╝

Usage: python manage.py <commande>

Commandes disponibles:

  stats       Affiche les statistiques du système
  reset       Réinitialise tout (pending + supprime Neo4j)
  scrape      Lance le scraping des entreprises en pending
  marketing   Lance l'agent marketing
  dashboard   Lance le dashboard web (http://localhost:5000)
  help        Affiche cette aide

Exemples:

  # Voir les statistiques
  python manage.py stats

  # Réinitialiser tout
  python manage.py reset

  # Scraper 40 entreprises
  python manage.py scrape

  # Lancer le marketing
  python manage.py marketing

  # Dashboard web
  python manage.py dashboard

Workflow complet:

  1. python manage.py reset      # Remettre à zéro
  2. python manage.py scrape     # Scraper les entreprises
  3. python manage.py marketing  # Générer les insights
  4. python manage.py dashboard  # Visualiser les résultats
""")


def main():
    """Point d'entrée principal."""
    if len(sys.argv) < 2:
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "stats":
        show_stats()
    
    elif command == "reset":
        reset_system()
    
    elif command == "scrape":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 40
        asyncio.run(run_scraper(limit))
    
    elif command == "marketing":
        asyncio.run(run_marketing())
    
    elif command == "dashboard":
        run_dashboard()
    
    elif command == "help":
        show_help()
    
    else:
        logger.error(f"❌ Commande inconnue: {command}")
        show_help()


if __name__ == "__main__":
    main()
