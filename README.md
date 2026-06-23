# 📧 Email Scraper RH

Récupère les adresses mail de **recrutement / RH / DSI** d'une liste de structures,
en visitant leurs sites officiels (page recrutement, carrières, contact).

Pensé au départ pour automatiser une recherche de stage/alternance en
Île-de-France, mais **entièrement générique** : tu fournis ta propre liste de
cibles dans un fichier CSV, et le script fait le reste.

> ⚠️ **Usage responsable.** Le script ne lit que des pages **publiques**. Les
> adresses collectées sont des données professionnelles soumises au **RGPD** :
> ne les rediffuse pas, ne constitue pas de fichier commercial, n'envoie pas de
> spam. À utiliser pour une démarche individuelle et légitime (candidature).
> Aucune donnée collectée n'est incluse dans ce dépôt.

---

## ✨ Ce que ça fait

Pour chaque organisation de ta liste, le script :

1. visite le site officiel ;
2. cherche la page **recrutement / carrières / contact** (liens + chemins classiques) ;
3. extrait les adresses mail (texte brut + liens `mailto:`) ;
4. les **score** pour faire remonter les mails RH/DSI les plus pertinents
   (`recrutement@`, `rh@`, `stage@`, domaine de l'établissement…) et écarter le
   bruit (`noreply@`, `webmaster@`…) ;
5. exporte un **CSV propre** trié par secteur + un **bloc copier-coller** dans le terminal.

Le tout en **asynchrone** (`asyncio` + `aiohttp`) → ~200 sites scrapés en quelques minutes.

---

## 📦 Contenu du dépôt

| Fichier | Rôle |
|---|---|
| `scraper_mails_rh.py` | Script principal — lit `cibles.csv`, scrape, exporte `resultats_mails.csv` |
| `email_finder.py` | Complément — pour les domaines **sans mail trouvé**, génère des patterns (`rh@`, `stage@`…) vérifiés par **handshake SMTP**, et cherche via **DuckDuckGo** |
| `cibles.example.csv` | Exemple de liste : **234 structures IDF, 14 secteurs** (à dupliquer en `cibles.csv` et adapter) |

---

## 🚀 Démarrage rapide

**Prérequis :** [Python 3.9+](https://www.python.org/downloads/) (vérifie avec `python --version`).

```bash
# 1. Récupérer le projet
git clone https://github.com/Nabil-42/email-scraper-rh.git
cd email-scraper-rh

# 2. (Recommandé) Créer un environnement virtuel
python -m venv .venv
source .venv/bin/activate             # Windows : .venv\Scripts\activate

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Créer ta liste de cibles à partir de l'exemple
cp cibles.example.csv cibles.csv      # Windows : copy cibles.example.csv cibles.csv
#   → édite cibles.csv avec TES structures

# 5. Lancer le scraping
python scraper_mails_rh.py
#   → génère resultats_mails.csv

# 6. (Optionnel) Compléter les domaines sans mail
python email_finder.py
#   → génère emails_supplementaires.csv
```

Si tu ne crées pas de `cibles.csv`, le script utilise automatiquement
`cibles.example.csv`.

---

## 📄 Format du fichier de cibles

CSV avec séparateur `;`, encodage UTF-8. Colonnes :

```csv
secteur;departement;nom;ville;categorie;site_web
SANTE_PUBLIC;75;AP-HP;Paris;CHU;https://recrutement.aphp.fr
ESN_GRANDE;92;Capgemini France;Issy-les-Moulineaux;ESN;https://www.capgemini.com/fr-fr/carrieres/
```

- **`nom`** est le seul champ obligatoire ; **`site_web`** est nécessaire pour scraper.
- **`secteur`** sert uniquement à regrouper les résultats (n'importe quelle valeur
  fonctionne ; les libellés connus sont juste plus jolis dans le récap).

### Secteurs de l'exemple fourni (Île-de-France, 234 structures)

| Secteur | Nb | Secteur | Nb |
|---|---|---|---|
| ESN moyennes | 31 | Organismes publics / Sécu | 16 |
| Santé privée (cliniques) | 35 | Administrations | 16 |
| Santé publique (AP-HP, CH) | 22 | Éditeurs IT santé | 15 |
| Grands groupes / industrie | 21 | ESN grandes | 14 |
| Cybersécurité | 14 | Cloud / Infra | 13 |
| Transport (RATP, SNCF, ADP) | 9 | Logement social / HLM | 13 |
| Médias / Enseignement sup. | 8 | Groupes santé | 7 |

---

## 📊 Sortie

`resultats_mails.csv` :

| Secteur | Departement | Nom | Ville | Mail RH 1 | Mail RH 2 | Site Web | Page Recrutement | Statut |
|---|---|---|---|---|---|---|---|---|

Et un récapitulatif terminal : bloc copier-coller par secteur + taux de réussite.

---

## 🔧 Personnalisation

Tout est en haut de `scraper_mails_rh.py` :

- `MAX_CONCURRENT` — nombre de sites scrapés en parallèle (défaut 15) ;
- `RH_KEYWORDS` — mots qui font monter le score d'un mail ;
- `RECRUTEMENT_PATHS` — chemins testés en fallback (`/recrutement`, `/carrieres`…) ;
- `EXCLUDE_DOMAINS` — domaines ignorés (réseaux sociaux, CDN…).

---

## 🛠️ Stack

Python 3.9+ · `asyncio` / `aiohttp` · `BeautifulSoup` + `lxml` · `dnspython` (optionnel, pour la résolution MX du `email_finder`).

## 📜 Licence

[MIT](LICENSE) — © 2026 Nabil Abboud
