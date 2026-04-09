# 🚀 Démarrage Rapide - SBT Intelligence

## En 4 Commandes

```bash
# 1. Voir les statistiques
python manage.py stats

# 2. Réinitialiser (40 entreprises en pending)
python manage.py reset

# 3. Scraper les entreprises
python manage.py scrape

# 4. Lancer le marketing
python manage.py marketing
```

## Dashboard Web

```bash
python manage.py dashboard
```

Ouvrir: http://localhost:5000

## Garanties

✅ **40 entreprises en entrée = 40 entreprises en sortie**  
✅ **Pas de doublons** (entreprises découvertes désactivées)  
✅ **Marketing relançable** sans réinitialisation  
✅ **Nouvelles recherches** n'affectent pas les anciennes

## Résultats Attendus

✅ **Emails**: 70-80% des entreprises  
✅ **Pays**: 90-95% des entreprises  
✅ **LinkedIn**: 40-60% des entreprises  
✅ **Export CSV**: `data/exports/prospects_SBT_*.csv`

## Dashboard Web

```bash
python -m src.web.app
```

Ouvrir: http://localhost:5000

## Documentation Complète

Voir `README.md` pour tous les détails.
