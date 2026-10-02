# DFS Recruitment & Testing

Application web interne de la **Digital Farming School** pour piloter une session de recrutement :
import des candidats, contrôle des dossiers via KoboToolbox, test de motivation (numéro de pièce + code examen),
puis test technique chronométré avec attribution des salles et ordinateurs.

Flask · PostgreSQL · SQLAlchemy 2 · Alembic · Gunicorn · Bootstrap 5 (servi localement, fonctionne sans Internet côté client) · Docker Compose.

---

## Sommaire

1. [Présentation](#présentation)
2. [Architecture](#architecture)
3. [Installation locale](#installation-locale)
4. [Configuration et variables d'environnement](#configuration-et-variables-denvironnement)
5. [Base PostgreSQL et migrations](#base-postgresql-et-migrations)
6. [Compte administrateur](#compte-administrateur)
7. [Import des candidats](#import-des-candidats)
8. [KoboToolbox](#kobotoolbox)
9. [Docker et Docker Compose](#docker-et-docker-compose)
10. [Déploiement Dokploy](#déploiement-dokploy)
11. [Sauvegardes](#sauvegardes)
12. [Tests](#tests)
13. [Dépannage](#dépannage)

---

## Présentation

| Rôle | Ce qu'il fait |
|---|---|
| `admin` | Tout : utilisateurs, imports, Kobo, salles/ordinateurs, exports, audit, paramètres, correction des données |
| `motivation_tester` | Voit les candidats, saisit **numéro de pièce + code examen** → « Motivation terminée » |
| `technical_tester` | Voit **uniquement** les candidats ayant terminé la motivation, attribue salle + ordinateur, pilote le chrono. Ne peut modifier ni l'identité, ni le code examen, ni le numéro de pièce |

Workflow : `import → synchro Kobo → motivation (pièce + code) → éligible technique → salle + PC libre + durée → chrono (pause / reprise / arrêt) → test terminé → PC libéré`.

Les permissions sont vérifiées **côté serveur** sur chaque route (`roles_required`), pas seulement dans le menu.

## Architecture

```text
app/
├── __init__.py            create_app(), /health, filtres Jinja, gestion d'erreurs, en-têtes de sécurité
├── config.py              configuration 100 % par variables d'environnement
├── extensions.py          db, migrate, login_manager, csrf
├── cli.py                 flask create-admin | seed | sync-kobo | list-users
├── utils.py               normalisation (téléphone, nom, CNI), RBAC, chiffrement du token Kobo
├── models/                User, Candidate, KoboSubmission, TechnicalTestSession, Room, Workstation, AuditLog, Setting
├── services/              logique métier (aucune requête HTTP)
│   ├── candidate_service  filtres, tri, validation motivation, codes examen, statistiques
│   ├── timer_service      attribution PC + chrono (verrous SQL, transitions d'état)
│   ├── import_service     lecture CSV/Excel, mapping, validation, doublons
│   ├── kobo_service       client API Kobo + synchronisation + correspondances
│   ├── settings_service   paramètres modifiables (table settings)
│   └── audit_service      journal d'audit
├── auth/ dashboard/ candidates/ motivation/ technical/ kobo/ admin/     Blueprints (routes fines)
├── templates/             Jinja2 (fragments `_*.html` rafraîchis par polling)
└── static/                CSS DFS, JS (polling + affichage chrono), Bootstrap/icônes/Poppins en local
migrations/                Alembic (0001_initial_schema)
tests/                     pytest (SQLite par défaut, PostgreSQL via TEST_DATABASE_URL)
```

### Règles métier critiques et où elles sont garanties

| Règle | Garantie |
|---|---|
| 1 & 2 — un code examen par candidat, un candidat par code | une seule colonne `candidates.exam_code` + contrainte `UNIQUE` |
| 3 — technique seulement après motivation | contrôle service + `CHECK (NOT technical_completed OR motivation_completed)` + routes 404 pour le testeur technique |
| 4 — le testeur technique ne modifie pas les données validées | routes de modification réservées à `admin` / `motivation_tester` |
| 5 — un PC ne sert qu'à un candidat à la fois | `SELECT … FOR UPDATE` sur le poste + **index unique partiel** `(workstation_id) WHERE status IN ('running','paused')` |
| 6 — une session active par candidat | index unique partiel `(candidate_id) WHERE status IN ('running','paused')` |
| 7 — chrono récupérable | `started_at`, `paused_at`, `total_pause_seconds`, `finished_at`, `duration_seconds` en base ; le navigateur n'affiche que le temps restant calculé par le serveur et se resynchronise toutes les 5 s |
| 8 — audit | `audit_logs` écrit dans la **même transaction** que l'action |

Statut global du candidat (Planifié → Présent → Dossier vérifié → Éligible technique → Technique en cours → Technique terminée) : **calculé**, jamais stocké.

« Temps réel » : polling AJAX de fragments HTML (5 s pour le technique, 10 s pour les dashboards). Pour passer plus tard à SSE/WebSocket, il suffit de remplacer `initPolling()` dans `static/js/app.js` ; les routes `?partial=1` restent valables.

### Routes principales

| URL | Rôles |
|---|---|
| `/login`, `/logout` | tous |
| `/` | dashboard selon le rôle |
| `/candidates/`, `/candidates/<id>` | tous (testeur technique : éligibles uniquement) |
| `/candidates/new`, `/<id>/edit`, `/<id>/delete` | admin |
| `/motivation/`, `/motivation/<id>/validate` | admin, motivation |
| `/technical/sessions`, `/technical/rooms`, `/technical/start/<id>`, `/technical/session/<id>/<pause\|resume\|stop\|complete>` | admin, technique |
| `/admin/users`, `/admin/rooms`, `/admin/imports`, `/admin/exports`, `/admin/audit`, `/admin/settings`, `/admin/kobo/` | admin |
| `/health` | public (JSON) |

## Installation locale

### Avec Docker (recommandé)

```bash
cp .env.example .env        # puis éditez SECRET_KEY, ADMIN_PASSWORD, POSTGRES_PASSWORD
docker compose up -d --build
```

Ouvrez <http://localhost:8000> et connectez-vous avec `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

Le port 8000 est déjà pris sur votre machine ? `WEB_PORT=8010 docker compose up -d` (ou ajoutez `WEB_PORT=8010` dans `.env`).

Données de démonstration (jamais lancées automatiquement) :

```bash
docker compose exec web flask seed      # 4 testeurs, 2 salles × 10 PC, 20 candidats du jour
```

Comptes de démo : `motivation1@demo.local`, `motivation2@demo.local`, `technique1@demo.local`, `technique2@demo.local`, mot de passe `Demo1234!`. Avec `FLASK_ENV=production`, la commande exige `--force`.

### Sans Docker (développement)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
export FLASK_APP=wsgi.py FLASK_ENV=development
export DATABASE_URL=postgresql+psycopg://dfs:motdepasse@localhost:5432/dfs
export ADMIN_EMAIL=admin@dfs.ci ADMIN_PASSWORD='Admin1234!'
flask db upgrade
flask create-admin
flask run --port 8000
```

## Configuration et variables d'environnement

| Variable | Rôle | Défaut |
|---|---|---|
| `FLASK_ENV` | `production` active cookies `Secure` et exige `SECRET_KEY` | `production` |
| `SECRET_KEY` | signe les sessions **et chiffre le token Kobo** (le changer oblige à ressaisir le token) | — obligatoire |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | administrateur initial (8 caractères min.) | — |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | base (conteneur `db` **et** connexion de l'app) | `dfs` / `dfs` / — |
| `DATABASE_URL` | URL complète, prioritaire sur `POSTGRES_*` | construite depuis `POSTGRES_*` |
| `SESSION_COOKIE_SECURE` | cookies uniquement en HTTPS (`localhost` est accepté par les navigateurs) | `true` en production |
| `KOBO_BASE_URL`, `KOBO_TOKEN`, `KOBO_ASSET_UID` | valeurs par défaut Kobo (l'écran admin est prioritaire) | `https://kf.kobotoolbox.org` |
| `DEFAULT_TECHNICAL_TEST_DURATION` | durée par défaut en minutes | `60` |
| `TIMEZONE` | fuseau d'affichage | `Africa/Abidjan` |
| `EXAM_CODE_PREFIX` | préfixe des codes générés | `DFS` |
| `MAX_UPLOAD_MB` | taille max d'un import | `10` |
| `WEB_CONCURRENCY` | workers Gunicorn (4 threads chacun) | `3` |
| `WEB_PORT` | port publié sur l'hôte | `8000` |

Le mot de passe PostgreSQL peut contenir des caractères spéciaux (`@ : / #`) : l'URL est construite et échappée par l'application.

Les paramètres « métier » (campagne, année, préfixe et format du code examen, durée, nom de l'application, fuseau) se modifient ensuite dans **Administration → Paramètres**. Format du code : `{prefix}-{year}-{seq:04d}` → `DFS-2026-0001`.

## Base PostgreSQL et migrations

```bash
flask db upgrade                       # appliquer (fait automatiquement au démarrage du conteneur)
flask db migrate -m "description"      # après modification d'un modèle
flask db check                         # vérifie que les modèles et la base sont alignés
```

`entrypoint.sh` exécute `flask db upgrade` puis `flask create-admin` avant Gunicorn ; les deux sont idempotents.
PostgreSQL n'est **jamais publié** hors du réseau Docker (pas de `ports:` sur `db`).

## Compte administrateur

Créé au démarrage **uniquement si aucun administrateur n'existe**, à partir de `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Aucun identifiant n'est codé en dur. Ensuite, changez les mots de passe depuis **Utilisateurs**.

```bash
docker compose exec web flask create-admin
docker compose exec web flask list-users
```

## Import des candidats

**Imports** → choisir un fichier `.csv`, `.xlsx` ou `.xls` (10 Mo max).

1. Les colonnes sont reconnues automatiquement, accents/majuscules/espaces ignorés :
   `N°/No/Numero/Numéro`, `Groupe`, `Date`, `Heure`, `Ordre`, `Nom et prénoms/Nom et prenoms/nom_prenoms`, `Date de naissance`, `Sexe`, `Email`, `Téléphone/Telephone/telephone`, `Ville`, `CNI`. Le mapping est modifiable (« Recalculer avec ce mapping »).
2. La prévisualisation affiche lignes, colonnes, valides, erreurs (nom manquant, date/email invalides, sexe inconnu, doublon interne) et doublons potentiels.
3. Doublons cherchés par **email → téléphone → CNI → nom + date de naissance**. Pour chacun : **Ignorer**, **Mettre à jour** (les champs vides du fichier n'effacent rien) ou **Examiner** (non importé, listé à la fin).
4. Rien n'est écrit avant **Confirmer l'importation**.

Normalisations : téléphones ramenés à 10 chiffres (`+225`, espaces et zéro initial perdu par Excel corrigés), dates `jj/mm/aaaa` ou Excel, sexe `M/F/Masculin/Féminin/H/Homme…`.

## KoboToolbox

**Administration → KoboToolbox** :

1. URL API (`https://kf.kobotoolbox.org` ou `https://kobo.humanitarianresponse.info`), **Asset UID** du formulaire (visible dans l'URL du projet Kobo), **token API** (Kobo → Paramètres du compte → Sécurité).
2. Noms des champs du formulaire : CNI, email, téléphone, code candidat, nom complet, statut dossier. Le nom court (`cni`) ou le chemin complet (`groupe_identite/cni`) fonctionnent.
3. **Tester la connexion Kobo**, puis **Synchroniser maintenant** (ou `flask sync-kobo`, utilisable dans un cron).

Correspondance, dans cet ordre : **CNI → Email → Téléphone → Code candidat → Nom complet**. Plusieurs candidats possibles → jamais d'association automatique : la soumission apparaît comme « Correspondance à vérifier » (dashboard admin + page Kobo) et l'admin l'associe à la main ; ce choix est conservé lors des synchronisations suivantes.

Valeurs du champ statut : `Oui/Conforme/Validé` → **Conforme** (vert), `Non/Non conforme` et `Non trouvé` → **Non conforme** (rouge), `À vérifier` ou valeur inconnue → **À vérifier** (orange), pas de soumission → **Non synchronisé** (gris). Sans champ statut configuré, une soumission trouvée vaut « Conforme ».

Le token est **chiffré en base** (Fernet, clé dérivée de `SECRET_KEY`) et n'est jamais renvoyé au navigateur.

## Docker et Docker Compose

* `Dockerfile` : `python:3.12-slim`, utilisateur non-root `appuser`, `HEALTHCHECK` sur `/health`, Gunicorn `gthread`.
* `docker-compose.yml` : `web` + `db` (`postgres:16-alpine`), volume `pgdata` persistant, volume `uploads`, healthcheck PostgreSQL, `web` attend `db` sain. Redis n'est pas nécessaire.

```bash
docker compose up -d --build      # démarrer / mettre à jour
docker compose logs -f web        # journaux
docker compose exec web flask list-users
docker compose down               # arrêter (les données restent dans le volume pgdata)
```

## Déploiement Dokploy

1. **Repository** : poussez ce dossier sur GitHub (le `.env` est ignoré par Git, ne le commitez jamais).
2. Dans Dokploy : **Create Project** → **Create Service** → **Compose**.
3. **Provider** : GitHub, choisissez le repository et la branche ; **Compose Path** : `./docker-compose.yml`.
4. Onglet **Environment** : collez le contenu de votre `.env` de production (Dokploy écrit le fichier `.env` utilisé par `env_file`). Générez `SECRET_KEY` avec `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Gardez `SESSION_COOKIE_SECURE=true`. Si le port 8000 de l'hôte est déjà occupé, ajoutez `WEB_PORT=` avec un autre port (ou supprimez le bloc `ports:` : Traefik n'en a pas besoin).
5. Onglet **Domains** : **Add Domain** → service `web`, port **8000**, votre domaine (ex. `recrutement.dfs.ci`), chemin `/`.
6. **HTTPS** : activez HTTPS avec le certificat **Let's Encrypt** (le DNS du domaine doit pointer vers le serveur Dokploy).
7. **Deploy**. Les journaux doivent montrer `Applying database migrations…`, `Administrateur … créé.` puis `Listening at: http://0.0.0.0:8000`.
8. Vérifiez `https://votre-domaine/health` → `{"status": "ok", "database": "connected"}`, puis connectez-vous.

Mises à jour : poussez sur la branche puis **Deploy** (ou activez l'auto-deploy) ; les migrations s'appliquent seules.

## Sauvegardes

Les données vivent dans le volume Docker `pgdata`. Sauvegarde (format custom compressé) :

```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > dfs_$(date +%F_%H%M).dump
```

Restauration (écrase les données existantes) :

```bash
docker compose stop web
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < dfs_2026-10-02_1800.dump
docker compose start web
```

Conseil pour les jours de recrutement : une sauvegarde le matin, à midi et le soir (cron sur l'hôte, ou **Backups** de Dokploy). Les exports Excel (**Exports**) servent aussi de copie lisible.

## Tests

```bash
pip install -r requirements.txt
pytest                                                   # SQLite en mémoire
TEST_DATABASE_URL=postgresql+psycopg://dfs:pw@localhost:5432/dfs_test pytest   # PostgreSQL réel
```

Couverture : authentification et rôles, CSRF, imports CSV/Excel/colonnes manquantes/doublons, motivation (pièce, format et unicité du code, génération), chrono (démarrage, pause, reprise, arrêt, fin, PC occupé, contrainte base), Kobo (pagination, correspondances, ambiguïtés, erreurs API, token chiffré).

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | un autre service utilise 8000 : `WEB_PORT=8010` dans `.env` |
| Connexion impossible en HTTP depuis un autre poste | `SESSION_COOKIE_SECURE=true` exige HTTPS : passez par le domaine HTTPS, ou mettez `false` sur un réseau local de confiance |
| `SECRET_KEY doit être défini en production` | renseignez `SECRET_KEY` dans `.env` |
| `ADMIN_EMAIL et ADMIN_PASSWORD doivent être définis` | variables manquantes au premier démarrage |
| « Session expirée » à l'envoi d'un formulaire | jeton CSRF expiré (déconnexion / redémarrage) : rechargez la page |
| Kobo « Token refusé » / « Formulaire introuvable » | vérifiez le token et l'Asset UID ; après changement de `SECRET_KEY`, ressaisissez le token |
| Kobo « Aucun champ de correspondance » | renseignez au moins un des champs CNI / email / téléphone / code / nom |
| `/health` renvoie 503 | base injoignable : `docker compose ps`, `docker compose logs db` |
| Le chrono semble décalé | l'affichage est recalé sur le serveur toutes les 5 s ; vérifiez l'heure du serveur (`date` dans le conteneur) |
