#!/usr/bin/env python3
"""
Email Finder — Complément au scraper principal
Deux méthodes pour trouver des emails supplémentaires :
  1. Patterns SMTP  : génère recrutement@domain.fr, rh@domain.fr, stage@domain.fr...
                      et vérifie si la boîte existe via handshake SMTP (sans envoyer)
  2. DuckDuckGo     : cherche "@domain.fr" recrutement sur le web, parfois
                      l'email est dans un PDF, un communiqué ou un autre site
Entrée  : resultats_mails.csv (généré par scraper_mails_rh.py)
Sortie  : emails_supplementaires.csv + bloc copier-coller dans le terminal

Installation : pip install requests beautifulsoup4 lxml dnspython
"""

import csv
import smtplib
import socket
import time
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── CONFIG ──────────────────────────────────────────────────────────────────
INPUT_CSV      = 'resultats_mails.csv'
OUTPUT_CSV     = 'emails_supplementaires.csv'
DEJA_ENVOYES   = 'emails_deja_envoyes.txt'

MAX_SMTP_WORKERS = 20
SMTP_TIMEOUT     = 7
DDG_DELAY        = 2.5   # secondes entre requêtes DuckDuckGo (évite le ban)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'fr-FR,fr;q=0.9',
}

EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')

# Patterns testés par domaine (ordre : du plus utile au moins utile)
PATTERNS_RH = [
    'recrutement', 'stage', 'stages', 'rh', 'drh',
    'candidature', 'candidatures', 'emploi',
    'dsi', 'informatique', 'direction', 'contact',
]

EXCLUDE_DOMAINS = {
    'example.com', 'test.com', 'google.com', 'facebook.com', 'linkedin.com',
    'twitter.com', 'instagram.com', 'youtube.com', 'sentry.io', 'w3.org',
}

# Mots = email inutilisable même s'il est trouvé
EXCLUDE_KEYWORDS = [
    'u003e', 'noreply', 'no-reply', 'donotreply', 'newsletter',
    'webmaster', 'dpo@', 'rgpd@', 'privacy@', 'security@',
    'ombuds', 'grc@', 'siem@',
]

# ─── CHARGEMENT CSV + EMAILS DEJA ENVOYES ────────────────────────────────────
def load_deja_envoyes():
    """Charge les emails déjà envoyés depuis le fichier texte."""
    emails = set()
    try:
        with open(DEJA_ENVOYES, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip().lower()
                if '@' in line and not line.startswith('#') and not line.startswith('='):
                    emails.add(line)
    except FileNotFoundError:
        pass
    return emails

def load_targets():
    """Charge les cibles depuis le CSV, extrait domaines et statuts."""
    targets = []
    seen_domains = set()
    try:
        with open(INPUT_CSV, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                site = row.get('Site Web', '').strip()
                if not site or not site.startswith('http'):
                    continue
                domain = urlparse(site).netloc.replace('www.', '').lower()
                if not domain or domain in EXCLUDE_DOMAINS or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                mail1 = row.get('Mail RH 1', '').strip()
                targets.append({
                    'nom':     row.get('Nom', ''),
                    'secteur': row.get('Secteur', ''),
                    'domain':  domain,
                    'status':  row.get('Statut', ''),
                    'has_mail': bool(mail1),
                })
    except FileNotFoundError:
        print(f"❌ {INPUT_CSV} introuvable — lance d'abord scraper_mails_rh.py")
        return []
    return targets

def is_email_valide(email, deja_envoyes):
    """Vérifie qu'un email est propre et pas déjà utilisé."""
    email = email.lower().strip()
    if any(kw in email for kw in EXCLUDE_KEYWORDS):
        return False
    if email in deja_envoyes:
        return False
    domain = email.split('@')[-1]
    if domain in EXCLUDE_DOMAINS:
        return False
    return True

# ─── MÉTHODE 1 : SMTP PATTERN VERIFICATION ───────────────────────────────────
_mx_cache = {}

def get_mx(domain):
    """Récupère le serveur MX du domaine."""
    if domain in _mx_cache:
        return _mx_cache[domain]
    result = None
    try:
        import dns.resolver
        records = dns.resolver.resolve(domain, 'MX')
        result = sorted(records, key=lambda r: r.preference)[0].exchange.to_text().rstrip('.')
    except ImportError:
        for prefix in ['mail', 'smtp', 'mx']:
            try:
                candidate = f"{prefix}.{domain}"
                socket.getaddrinfo(candidate, 25, socket.AF_INET, socket.SOCK_STREAM)
                result = candidate
                break
            except Exception:
                pass
        if not result:
            result = domain
    except Exception:
        pass
    _mx_cache[domain] = result
    return result

def verify_smtp(email):
    """
    Vérifie via handshake SMTP si l'adresse existe.
    Retourne : True | False | None (port bloqué ou catch-all)
    """
    domain = email.split('@')[1]
    mx = get_mx(domain)
    if not mx:
        return None
    try:
        with smtplib.SMTP(mx, 25, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo('candidature.stage.fr')
            smtp.mail('candidature@stage-info.fr')
            code, msg = smtp.rcpt(email)
            try:
                smtp.quit()
            except Exception:
                pass
            # 250 = existe, 251 = forward, 550/551/553 = n'existe pas
            if code == 250 or code == 251:
                return True
            elif code >= 550:
                return False
            else:
                return None  # Réponse ambiguë
    except (ConnectionRefusedError, socket.timeout, OSError):
        return None  # Port 25 bloqué par le FAI (fréquent sur réseau domestique)
    except smtplib.SMTPException:
        return None
    except Exception:
        return None

def run_smtp_patterns(targets, deja_envoyes):
    """Lance la vérification SMTP en parallèle pour tous les domaines."""
    print("📡 Phase 1 — Vérification SMTP des patterns RH...")
    print("   (Si port 25 bloqué par ton FAI, on passe directement à DuckDuckGo)\n")

    # Génère tous les emails à tester
    all_emails = []
    for t in targets:
        if t['has_mail']:
            continue  # Déjà un email trouvé par le scraper
        for pattern in PATTERNS_RH:
            email = f"{pattern}@{t['domain']}"
            if is_email_valide(email, deja_envoyes):
                all_emails.append((email, t))

    if not all_emails:
        print("   Aucun email à tester.\n")
        return [], False

    print(f"   {len(all_emails)} combinaisons à tester...")

    resultats = []
    port_bloque = False
    verifies = 0

    with ThreadPoolExecutor(max_workers=MAX_SMTP_WORKERS) as executor:
        future_map = {executor.submit(verify_smtp, email): (email, t)
                      for email, t in all_emails}

        for future in as_completed(future_map):
            email, target = future_map[future]
            verifies += 1
            try:
                result = future.result()
                if result is None and not port_bloque:
                    port_bloque = True
                    print("   ⚠️  Port 25 bloqué — SMTP ne peut pas vérifier depuis ce réseau")
                elif result is True:
                    print(f"   ✅ {email:<45}  # {target['nom'][:35]}")
                    resultats.append({
                        'email': email,
                        'nom': target['nom'],
                        'secteur': target['secteur'],
                        'domain': target['domain'],
                        'methode': 'SMTP',
                        'statut': 'verifie',
                    })
            except Exception:
                pass

    print(f"\n   → {len(resultats)} email(s) trouvés via SMTP\n")
    return resultats, port_bloque

# ─── MÉTHODE 2 : DUCKDUCKGO SEARCH ───────────────────────────────────────────
def search_ddg(query):
    """Lance une recherche DuckDuckGo et retourne les emails trouvés."""
    emails = set()
    try:
        url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, 'lxml')

        # Cherche dans les snippets de résultats
        for snippet in soup.select('.result__snippet, .result__body, .result__url'):
            text = snippet.get_text()
            for m in EMAIL_REGEX.finditer(text):
                emails.add(m.group().lower())

        # Cherche aussi dans le HTML brut (emails parfois dans les meta)
        for m in EMAIL_REGEX.finditer(r.text):
            emails.add(m.group().lower())

    except Exception:
        pass
    return emails

def run_duckduckgo(targets, deja_envoyes, deja_trouves):
    """Recherche DuckDuckGo pour chaque domaine sans email."""
    cibles_ddg = [t for t in targets if not t['has_mail']]
    print(f"🦆 Phase 2 — DuckDuckGo ({len(cibles_ddg)} domaines, {DDG_DELAY}s entre requêtes)...\n")

    resultats = []
    total = len(cibles_ddg)

    for i, target in enumerate(cibles_ddg, 1):
        domain = target['domain']
        nouveaux = set()

        queries = [
            f'"{domain}" recrutement OR stage OR emploi',
            f'"{domain}" rh OR drh OR candidature',
        ]

        for query in queries:
            raw_emails = search_ddg(query)
            for email in raw_emails:
                if domain not in email.split('@')[-1]:
                    continue  # On garde seulement les emails du domaine cible
                if not is_email_valide(email, deja_envoyes):
                    continue
                if email in deja_trouves:
                    continue
                nouveaux.add(email)
            time.sleep(DDG_DELAY)

        for email in nouveaux:
            print(f"   🦆 {email:<45}  # {target['nom'][:35]}")
            deja_trouves.add(email)
            resultats.append({
                'email': email,
                'nom': target['nom'],
                'secteur': target['secteur'],
                'domain': domain,
                'methode': 'DuckDuckGo',
                'statut': 'a_verifier',
            })

        if i % 20 == 0:
            print(f"   ... {i}/{total} domaines traités")

    print(f"\n   → {len(resultats)} email(s) trouvés via DuckDuckGo\n")
    return resultats

# ─── EXPORT + BLOC COPIER-COLLER ─────────────────────────────────────────────
def export_et_affiche(resultats):
    if not resultats:
        print("😔 Aucun nouvel email trouvé.")
        print("   → Les sites utilisent probablement des formulaires de contact.")
        return

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(['Email', 'Nom', 'Secteur', 'Domaine', 'Méthode', 'Statut'])
        for r in sorted(resultats, key=lambda x: x['secteur']):
            writer.writerow([
                r['email'], r['nom'], r['secteur'],
                r['domain'], r['methode'], r['statut'],
            ])

    print("=" * 65)
    print("BLOC COPIER-COLLER — NOUVEAUX EMAILS")
    print("=" * 65)

    verifies  = [r for r in resultats if r['statut'] == 'verifie']
    a_verifier = [r for r in resultats if r['statut'] == 'a_verifier']

    if verifies:
        print("\n✅ Vérifiés par SMTP (boîte confirmée) :")
        print("; ".join(r['email'] for r in verifies))

    if a_verifier:
        print("\n🦆 Trouvés par DuckDuckGo (à utiliser mais quelques bounces possibles) :")
        print("; ".join(r['email'] for r in a_verifier))

    print(f"\n📂 Détail complet : {OUTPUT_CSV}")
    print(f"📊 Total nouveaux : {len(resultats)} "
          f"({len(verifies)} vérifiés SMTP + {len(a_verifier)} DuckDuckGo)")

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print("🔍 EMAIL FINDER — Patterns SMTP + DuckDuckGo")
    print("=" * 65)

    deja_envoyes = load_deja_envoyes()
    print(f"📋 {len(deja_envoyes)} adresses déjà envoyées chargées (doublon protection)\n")

    targets = load_targets()
    if not targets:
        return
    print(f"🏢 {len(targets)} domaines uniques à explorer\n")

    # Phase 1 : SMTP
    smtp_results, port_bloque = run_smtp_patterns(targets, deja_envoyes)
    deja_trouves = {r['email'] for r in smtp_results}

    # Phase 2 : DuckDuckGo
    ddg_results = run_duckduckgo(targets, deja_envoyes, deja_trouves)

    # Fusion et export
    tous = smtp_results + ddg_results
    export_et_affiche(tous)

if __name__ == '__main__':
    main()
