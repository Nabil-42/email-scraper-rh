#!/usr/bin/env python3
"""
Scraper de mails RH / recrutement à partir d'une liste de structures.

Pour chaque organisation listée dans un fichier CSV (nom, secteur, site web),
le script :
  1. visite le site officiel,
  2. cherche la page recrutement / carrières / contact,
  3. extrait les adresses mail (texte + liens mailto:),
  4. les score pour faire remonter les mails RH/DSI les plus pertinents,
  5. exporte un CSV propre + un bloc copier-coller par secteur.

ENTRÉE  : un fichier CSV de cibles (par défaut `cibles.csv`, sinon
          `cibles.example.csv` fourni en exemple).
          Colonnes attendues (séparateur `;`) :
              secteur;departement;nom;ville;categorie;site_web
SORTIE  : resultats_mails.csv + récapitulatif dans le terminal.

⚠️  Usage responsable : ce script ne lit que des pages publiques. Respecte
    le RGPD et les CGU des sites. Les mails collectés sont des données à
    caractère professionnel — ne les rediffuse pas et n'envoie pas de spam.

Installation : pip install -r requirements.txt
Utilisation  : python scraper_mails_rh.py
"""

import asyncio
import aiohttp
import re
import csv
import os
import sys
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# ─── CONFIG ───────────────────────────────────────────────────────────────────
# Fichier de cibles : ta liste perso (cibles.csv) sinon l'exemple fourni.
CIBLES_CSV = 'cibles.csv' if os.path.exists('cibles.csv') else 'cibles.example.csv'
OUTPUT_CSV = 'resultats_mails.csv'
MAX_CONCURRENT = 15
TIMEOUT = 12
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8',
}

RECRUTEMENT_PATHS = [
    '/recrutement', '/recrutement/', '/recruter', '/recruter/',
    '/nous-rejoindre', '/nous-rejoindre/',
    '/carrieres', '/carrieres/', '/carriere', '/carriere/',
    '/emploi', '/emploi/', '/jobs', '/offres-emploi',
    '/travailler-avec-nous', '/rejoindre', '/rejoindre-nous',
    '/stages', '/alternance', '/offres-de-stage',
    '/rh', '/ressources-humaines',
    '/contact', '/contact/',
]

EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')

EXCLUDE_DOMAINS = {
    'example.com', 'exemple.fr', 'test.com', 'sentry.io',
    'w3.org', 'schema.org', 'google.com', 'facebook.com',
    'twitter.com', 'linkedin.com', 'instagram.com', 'youtube.com',
    'cnil.fr', 'data.gouv.fr', 'legifrance.gouv.fr',
    'apple.com', 'microsoft.com', 'adobe.com', 'amazonaws.com',
    'jquery.com', 'wordpress.com', 'wixsite.com',
}

RH_KEYWORDS = [
    'recrutement', 'recrut', 'drh', 'rh@', 'candidature',
    'emploi', 'formation', 'personnel', 'stage', 'stagiaire',
    'alternance', 'apprenti', 'ressources-humaines', 'ressources_humaines',
    'dsi', 'informatique', 'it@', 'numerique', 'digital',
    'direction', 'contact', 'carrieres',
]

# Libellés lisibles par secteur (le bloc final reste correct même pour un
# secteur inconnu : on retombe sur le code brut).
SECTEUR_LABELS = {
    'SANTE_PUBLIC':     'Santé publique (AP-HP, CH...)',
    'SANTE_PRIVE':      'Santé privée (cliniques)',
    'SANTE_GROUPE':     'Groupes santé (Ramsay, Elsan...)',
    'EDITEUR_SANTE':    'Éditeurs IT santé (Dedalus, Sesan...)',
    'ESN_GRANDE':       'ESN grandes (Capgemini, Atos...)',
    'ESN_MOYENNE':      'ESN moyennes (Devoteam, Aubay...)',
    'CYBER':            'Cybersécurité',
    'TRANSPORT':        'Transport (RATP, SNCF, ADP)',
    'ORGANISME_PUBLIC': 'Organismes publics (CAF, CPAM, CEA...)',
    'LOGEMENT_SOCIAL':  'Logement social (OPH, HLM...)',
    'GRAND_GROUPE':     'Grands groupes (EDF, Orange, BNP...)',
    'ADMINISTRATION':   'Administrations / Ministères',
    'CLOUD_INFRA':      'Cloud & Infrastructure (OVH, MS, AWS...)',
    'MEDIA_EDU':        'Médias & Enseignement supérieur',
}


# ─── CHARGEMENT DES CIBLES ────────────────────────────────────────────────────
def load_cibles(path=CIBLES_CSV):
    """
    Charge les cibles depuis un CSV `;`.
    Colonnes attendues : secteur, departement, nom, ville, categorie, site_web.
    Seul `nom` est obligatoire ; `site_web` est nécessaire pour scraper.
    """
    cibles = []
    if not os.path.exists(path):
        print(f"❌ Fichier de cibles introuvable : {path}")
        print("   Crée un `cibles.csv` (voir `cibles.example.csv` fourni).")
        return []

    with open(path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            nom = (row.get('nom') or '').strip()
            if not nom:
                continue
            cibles.append({
                'nom':         nom,
                'ville':       (row.get('ville') or '').strip(),
                'departement': (row.get('departement') or '').strip(),
                'categorie':   (row.get('categorie') or '').strip(),
                'secteur':     (row.get('secteur') or 'AUTRE').strip() or 'AUTRE',
                'site_web':    (row.get('site_web') or '').strip(),
            })
    return cibles


# ─── SCRAPING ─────────────────────────────────────────────────────────────────
def score_email(email, nom_etab):
    score = 0
    email_lower = email.lower()
    for kw in RH_KEYWORDS:
        if kw in email_lower:
            score += 10
    domain = email.split('@')[-1].replace('.fr', '').replace('.com', '')
    nom_mots = [m.lower() for m in nom_etab.split() if len(m) > 3]
    for mot in nom_mots:
        if mot[:6] in domain:
            score += 5
    if any(x in email_lower for x in ['noreply', 'no-reply', 'webmaster', 'newsletter', 'donotreply']):
        score -= 5
    return score


def extract_emails_from_html(html, base_url=''):
    emails = set()
    for m in EMAIL_REGEX.finditer(html):
        email = m.group().lower()
        domain = email.split('@')[-1]
        if domain not in EXCLUDE_DOMAINS and len(email) < 80 and '.' in domain:
            emails.add(email)
    try:
        soup = BeautifulSoup(html, 'lxml')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('mailto:'):
                email = href[7:].split('?')[0].lower().strip()
                if '@' in email and len(email) < 80:
                    emails.add(email)
    except Exception:
        pass
    return emails


async def fetch_page(session, url):
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            headers=HEADERS,
            allow_redirects=True,
            ssl=False
        ) as resp:
            if resp.status == 200:
                ct = resp.headers.get('content-type', '')
                if 'html' in ct or not ct:
                    return await resp.text(errors='replace')
    except Exception:
        pass
    return None


async def scrape_cible(session, cible):
    result = {
        'nom': cible['nom'],
        'ville': cible['ville'],
        'departement': cible['departement'],
        'categorie': cible['categorie'],
        'secteur': cible['secteur'],
        'mails_rh': [],
        'mails_autres': [],
        'site_web': cible.get('site_web', ''),
        'page_recrutement': '',
        'statut': 'non_traite',
    }

    site = cible.get('site_web', '').strip()
    if not site:
        result['statut'] = 'pas_de_site'
        return result
    if not site.startswith('http'):
        site = 'https://' + site

    html_main = await fetch_page(session, site)
    if not html_main:
        site_http = site.replace('https://', 'http://')
        html_main = await fetch_page(session, site_http)

    if not html_main:
        result['statut'] = 'inaccessible'
        return result

    all_emails = extract_emails_from_html(html_main, site)

    # Cherche la page recrutement via les liens
    soup = BeautifulSoup(html_main, 'lxml')
    recrutement_url = None
    for a in soup.find_all('a', href=True):
        href = a['href'].lower()
        text = a.get_text().lower()
        if any(kw in href or kw in text for kw in ['recrut', 'emploi', 'carrieres', 'rejoindre', 'travailler', 'stage', 'alternance']):
            full_url = urljoin(site, a['href'])
            if urlparse(full_url).netloc == urlparse(site).netloc:
                recrutement_url = full_url
                break

    # Fallback sur les chemins classiques
    if not recrutement_url:
        for path in RECRUTEMENT_PATHS:
            candidate = site.rstrip('/') + path
            html_rec = await fetch_page(session, candidate)
            if html_rec and len(html_rec) > 300:
                recrutement_url = candidate
                all_emails |= extract_emails_from_html(html_rec, site)
                break
    elif recrutement_url:
        html_rec = await fetch_page(session, recrutement_url)
        if html_rec:
            all_emails |= extract_emails_from_html(html_rec, site)

    result['page_recrutement'] = recrutement_url or ''

    domain_etab = urlparse(site).netloc.replace('www.', '')
    mails_rh = []
    mails_autres = []

    for email in all_emails:
        domain_mail = email.split('@')[-1]
        score = score_email(email, cible['nom'])
        if domain_mail in domain_etab or domain_etab in domain_mail:
            if score >= 0:
                mails_rh.append((score, email))
        elif score > 5:
            mails_rh.append((score, email))
        elif '@' in email and score >= -2:
            mails_autres.append(email)

    mails_rh.sort(reverse=True)
    result['mails_rh'] = [e for _, e in mails_rh[:5]]
    result['mails_autres'] = list(set(mails_autres))[:3]
    result['statut'] = 'ok' if mails_rh else ('ok_sans_mail' if html_main else 'erreur')

    return result


# ─── ORCHESTRATION ────────────────────────────────────────────────────────────
async def scrape_all(cibles):
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    connector = aiohttp.TCPConnector(ssl=False, limit=MAX_CONCURRENT)

    async with aiohttp.ClientSession(connector=connector) as session:

        async def scrape_with_sem(c):
            async with semaphore:
                r = await scrape_cible(session, c)
                icon = "✅" if r['mails_rh'] else ("⚠️ " if r['statut'] == 'ok_sans_mail' else "❌")
                mails_str = ", ".join(r['mails_rh'][:2]) if r['mails_rh'] else r['statut']
                sect = r['secteur'][:12]
                print(f"  {icon} [{sect:<12}] {r['nom'][:40]:<40} → {mails_str}")
                return r

        tasks = [scrape_with_sem(c) for c in cibles]
        return await asyncio.gather(*tasks)


# ─── EXPORT CSV ───────────────────────────────────────────────────────────────
def export_csv(results):
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow([
            'Secteur', 'Departement', 'Nom', 'Ville', 'Categorie',
            'Mail RH 1', 'Mail RH 2', 'Mail RH 3',
            'Site Web', 'Page Recrutement', 'Statut'
        ])
        for r in sorted(results, key=lambda x: (x['secteur'], x['departement'], x['nom'])):
            mails = r['mails_rh'] + [''] * 3
            writer.writerow([
                r['secteur'], r['departement'], r['nom'], r['ville'], r['categorie'],
                mails[0], mails[1], mails[2],
                r['site_web'], r['page_recrutement'], r['statut'],
            ])
    print(f"\n✅ CSV exporté : {os.path.abspath(OUTPUT_CSV)}")


# ─── BLOC FINAL ───────────────────────────────────────────────────────────────
def print_bloc_final(results):
    print("\n" + "=" * 65)
    print("BLOC COPIER-COLLER — MAILS RH/RECRUTEMENT CONFIRMÉS")
    print("=" * 65)

    # Secteurs réellement présents dans les résultats (ordre stable)
    secteurs_presents = []
    for r in results:
        if r['secteur'] not in secteurs_presents:
            secteurs_presents.append(r['secteur'])

    total_mails = 0
    for secteur in secteurs_presents:
        sect_results = [r for r in results if r['secteur'] == secteur and r['mails_rh']]
        if not sect_results:
            continue
        label = SECTEUR_LABELS.get(secteur, secteur)
        print(f"\n── {label} ──")
        for r in sorted(sect_results, key=lambda x: x['nom']):
            for mail in r['mails_rh'][:2]:
                print(f"  {mail}  # {r['nom']}")
                total_mails += 1

    print("\n" + "=" * 65)

    # Stats globales
    total = len(results)
    avec_mail = sum(1 for r in results if r['mails_rh'])
    sans_site = sum(1 for r in results if r['statut'] == 'pas_de_site')
    inaccessible = sum(1 for r in results if r['statut'] == 'inaccessible')

    print(f"\n📊 Stats globales :")
    print(f"  Cibles scrapées      : {total}")
    print(f"  Avec mail RH trouvé  : {avec_mail}")
    print(f"  Mails uniques en tout: {total_mails}")
    print(f"  Sans site web        : {sans_site}")
    print(f"  Sites inaccessibles  : {inaccessible}")

    print(f"\n📂 Fichier CSV complet : {os.path.abspath(OUTPUT_CSV)}")

    # Taux de réussite par secteur
    print(f"\n📊 Taux de réussite par secteur :")
    for secteur in secteurs_presents:
        total_s = sum(1 for r in results if r['secteur'] == secteur)
        ok_s = sum(1 for r in results if r['secteur'] == secteur and r['mails_rh'])
        if total_s:
            pct = int(ok_s / total_s * 100)
            print(f"  {secteur:<20} : {ok_s:>3}/{total_s:<3} ({pct}%)")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
async def main():
    print("🎯 SCRAPER MAILS RH / RECRUTEMENT")
    print("=" * 65)

    cibles = load_cibles()
    if not cibles:
        sys.exit(1)

    # Dédoublonnage par nom
    seen = set()
    cibles_uniq = []
    for c in cibles:
        key = c['nom'].lower().strip()
        if key not in seen:
            seen.add(key)
            cibles_uniq.append(c)

    nb_secteurs = len(set(c['secteur'] for c in cibles_uniq))
    print(f"📄 Source : {CIBLES_CSV}")
    print(f"🔍 {len(cibles_uniq)} organisations à scraper ({nb_secteurs} secteurs)\n")

    results = await scrape_all(cibles_uniq)

    export_csv(results)
    print_bloc_final(results)


if __name__ == '__main__':
    asyncio.run(main())
