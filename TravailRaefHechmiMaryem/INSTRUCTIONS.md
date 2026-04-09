# 📖 Instructions - SBT Intelligence

## 🎯 Problème Résolu

**Avant**: 40 entreprises en entrée → 80 entreprises en sortie (doublons)

**Maintenant**: 40 entreprises en entrée = 40 entreprises en sortie ✅

## 🚀 Utilisation Simple

### 1. Voir les Statistiques

```bash
python manage.py stats
```

Affiche:
- Nombre d'entreprises (total, pending, scraped, error)
- Qualité des données (email, téléphone, LinkedIn)
- Répartition par tier dans Neo4j

### 2. Réinitialiser le Système

```bash
python manage.py reset
```

**Ce que ça fait**:
- Remet toutes les entreprises en statut "pending"
- Supprime toutes les raw_company
- Vide le graphe Neo4j
- Demande confirmation avant d'agir

**Quand l'utiliser**:
- Avant de relancer un nouveau cycle de scraping
- Pour corriger les doublons existants
- Pour repartir de zéro

### 3. Scraper les Entreprises

```bash
# Scraper toutes les entreprises en pending (automatique)
python manage.py scrape

# Ou limiter à un nombre spécifique
python manage.py scrape 20
```

**Ce que ça fait**:
- Scrape UNIQUEMENT les entreprises en statut "pending"
- Par défaut, traite TOUTES les entreprises en pending
- Extrait: email, téléphone, pays, LinkedIn, description
- Crée les entreprises dans Neo4j
- Change le statut en "scraped"

**Garanties**:
- Ne touche pas aux entreprises déjà scrapées
- Pas de doublons (entreprises découvertes désactivées)
- 40 pending → 40 scraped (automatique)

**Dashboard Web**:
- Le bouton "Lancer le scraping" traite automatiquement TOUTES les pending
- Plus besoin de spécifier une limite

### 4. Lancer le Marketing

```bash
python manage.py marketing
```

**Ce que ça fait**:
- Analyse les entreprises scrapées
- Détecte les opportunités (fournisseurs potentiels)
- Génère un export CSV avec tous les détails
- Peut être relancé plusieurs fois

**Fichier généré**:
- `data/exports/prospects_SBT_YYYYMMDD_HHMM.csv`

### 5. Dashboard Web

```bash
python manage.py dashboard
```

**Ce que ça fait**:
- Lance le serveur web sur http://localhost:5000
- Affiche les statistiques en temps réel
- Visualise le graphe Neo4j
- Permet de lancer les agents depuis l'interface

**Pages disponibles**:
- `/` - Accueil avec statistiques
- `/dashboard` - Liste des entreprises
- `/graph` - Visualisation du graphe
- `/marketing` - Insights marketing

## 📊 Workflow Complet

```bash
# 1. Réinitialiser (si nécessaire)
python manage.py reset

# 2. Scraper les entreprises
python manage.py scrape

# 3. Générer les insights
python manage.py marketing

# 4. Visualiser
python manage.py dashboard
```

## ⚠️ Important

### Nouvelles Recherches

Si vous lancez une nouvelle recherche avec le Target Agent:
- Les nouvelles entreprises sont ajoutées en "pending"
- Les anciennes entreprises ne sont PAS affectées
- Le scraper ne traite QUE les "pending"

### Relancer le Marketing

Vous pouvez relancer le marketing autant de fois que vous voulez:
- Pas besoin de réinitialiser
- Génère un nouveau CSV à chaque fois
- Analyse toujours les mêmes entreprises scrapées

### Corriger les Doublons

Si vous avez des doublons (40 → 80):
```bash
python manage.py reset
python manage.py scrape
```

## 🔍 Vérification

### Vérifier qu'il n'y a pas de doublons

```bash
python manage.py stats
```

Vous devriez voir:
- **Scraped**: 40 (ou le nombre d'entreprises scrapées)
- **Neo4j Entreprises**: 40 (même nombre)
- **Raw companies**: 40 (même nombre)

Si les nombres sont différents, il y a des doublons. Utilisez `reset` puis `scrape`.

## 💡 Conseils

1. **Toujours vérifier les stats** avant et après chaque opération
2. **Réinitialiser** si vous voyez des doublons
3. **Le marketing** peut être relancé sans problème
4. **Le dashboard** affiche uniquement les entreprises scrapées (avec website)

## 🐛 Dépannage

### "Aucune entreprise en pending"

**Solution**: Lancez une nouvelle recherche avec le Target Agent, ou réinitialisez:
```bash
python manage.py reset
```

### "Trop d'entreprises dans Neo4j"

**Solution**: Réinitialisez pour supprimer les doublons:
```bash
python manage.py reset
python manage.py scrape
```

### "Le marketing ne génère rien"

**Solution**: Vérifiez qu'il y a des entreprises scrapées:
```bash
python manage.py stats
```

Si "Scraped" = 0, lancez d'abord le scraper:
```bash
python manage.py scrape
```

## 📖 Documentation Complète

Voir `README.md` pour:
- Architecture détaillée
- Configuration
- APIs
- Exemples avancés

## 🎉 Résumé

Le système est maintenant:
- ✅ Simple (1 seul script)
- ✅ Sans doublons (40 → 40)
- ✅ Relançable (marketing multiple)
- ✅ Propre (fichiers inutiles supprimés)

**Commande la plus importante**:
```bash
python manage.py help
```
