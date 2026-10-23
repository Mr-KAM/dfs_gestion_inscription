# DFS Recruitment & Testing

Application web interne de la **Digital Farming School** pour piloter le recrutement puis la vie des étudiants :

* **Recrutement** : import des candidats, contrôle des dossiers via KoboToolbox, test de motivation (numéro de pièce + code examen), test technique chronométré avec attribution des salles et ordinateurs, validation des résultats.
* **SAS** (étape suivante du recrutement) : admission des candidats retenus, classes, chambres, matériel, appels de présence avec heures d'absence.
* **Vie du SAS** : coachs (appels, notifications, exercices), accueil (registre des passages, problèmes étudiants), cité (infrastructures, inventaire, consommables).
* **Espace candidat / étudiant** : convocation, QR code personnel, présence, classe, exercices, notifications, profil et photo.

Flask · PostgreSQL · SQLAlchemy 2 · Alembic · Gunicorn · Bootstrap 5 (CSS, icônes et polices servis localement, fonctionne sans Internet côté client) · Docker Compose.

---

## Sommaire

1. [Présentation et rôles](#présentation-et-rôles)
2. [Architecture](#architecture)
3. [Installation locale](#installation-locale)
4. [Configuration et variables d'environnement](#configuration-et-variables-denvironnement)
5. [Base PostgreSQL et migrations](#base-postgresql-et-migrations)
6. [Comptes : administrateur, personnel, profils](#comptes--administrateur-personnel-profils)
7. [Espace candidat](#espace-candidat)
8. [Recrutement : imports, validation des tests, matricule et QR code](#recrutement--imports-validation-des-tests-matricule-et-qr-code)
9. [KoboToolbox](#kobotoolbox)
10. [SAS : admission, classes, chambres, matériel](#sas--admission-classes-chambres-matériel)
11. [Appels de présence et heures d'absence](#appels-de-présence-et-heures-dabsence)
12. [Vie du SAS : coachs, accueil, problèmes](#vie-du-sas--coachs-accueil-problèmes)
13. [Infrastructures, inventaire et consommables](#infrastructures-inventaire-et-consommables)
14. [Docker et Docker Compose](#docker-et-docker-compose)
15. [Déploiement Dokploy](#déploiement-dokploy)
16. [Sauvegardes](#sauvegardes)
17. [Tests](#tests)
18. [Sécurité](#sécurité)
19. [Dépannage](#dépannage)

---

## Présentation et rôles

| Rôle | Ce qu'il fait |
|---|---|
| `admin` | Tout : utilisateurs, imports, Kobo, salles/ordinateurs, SAS, vie du SAS, infrastructures, exports, audit, paramètres, correction des données |
| `supervisor` | **Superviseur** : cumule testeur motivation et testeur technique (voit tous les candidats, valide la motivation, pilote les tests) avec un dashboard combiné. **Aucun droit d'administration** |
| `motivation_tester` | Voit les candidats, saisit **numéro de pièce + code examen** → « Motivation terminée », coche « Validé » |
| `technical_tester` | Voit **uniquement** les candidats ayant terminé la motivation, attribue salle + ordinateur, pilote le chrono, coche « Validé » |
| `coach` | **Coach technique** (instructeur permanent) : crée ses classes et y invite des étudiants, lance les appels de ses classes, envoie des notifications, crée des exercices programmés et valide les résultats |
| `reception` | **Responsable accueil** : registre des passages sur le site (visites, autres activités), liste et profils des étudiants du SAS, absences, problèmes étudiants, notifications |
| `residence` | **Responsable de cité** : infrastructures (bâtiments, niveaux, salles), inventaire du matériel, consommables, chambres / matériel des étudiants du SAS |
| `candidate` | **Candidat / étudiant** : se connecte sur `/candidat/connexion` et ne voit que **son propre** espace. Aucun accès aux pages du personnel |

Workflow :
`import → synchro Kobo → motivation (pièce + code) → éligible technique → salle + PC + durée → chrono → test terminé → validé → admission au SAS → classe, chambre, matériel → appels, exercices, vie du campus`.

Les permissions sont vérifiées **côté serveur** sur chaque route (`roles_required` + contrôles par classe), pas seulement dans le menu.
Rôles de recrutement : `RECRUITMENT_ROLES` ; rôles du SAS : `CAMPUS_ROLES` (`app/models/user.py`).

## Architecture

```text
app/
├── __init__.py            create_app(), /health, filtres Jinja, gestion d'erreurs, en-têtes de sécurité
├── config.py              configuration 100 % par variables d'environnement
├── extensions.py          db, migrate, login_manager, csrf
├── cli.py                 flask create-admin | seed | sync-kobo | list-users | create-candidate-accounts
├── utils.py               normalisation (téléphone, nom, CNI), RBAC, chiffrement du token Kobo, droit caméra
├── models/
│   ├── user, candidate, kobo, technical_test, workstation, audit      recrutement, comptes, audit
│   ├── sas                SAS (cohorte) ; classe / chambre / matériel sont sur le candidat
│   ├── attendance         appels (AttendanceSession) et présences (Attendance)
│   ├── campus             notifications, exercices + réponses, registre des passages, problèmes, classes + invitations
│   └── infra              bâtiments, niveaux, salles, matériel, consommables + mouvements de stock
├── services/              logique métier (aucune requête HTTP)
│   ├── candidate_service  filtres, tri, motivation, codes examen, matricule, QR code chiffré, statistiques
│   ├── account_service    comptes candidats, inscription, mots de passe, profils et photos
│   ├── timer_service      attribution PC + chrono (verrous SQL, transitions d'état)
│   ├── import_service     imports CSV/Excel : candidats, résultats des tests, salles
│   ├── kobo_service       client API Kobo + synchronisation + correspondances
│   ├── sas_service        filtres, import et attribution automatique classes / chambres / matériel
│   ├── attendance_service appels, QR code tournant, marquage, heures d'absence, rapport SAS
│   ├── campus_service     classes, invitations, notifications, exercices, registre, problèmes
│   ├── infra_service      infrastructures, inventaire, historique, stock
│   ├── settings_service   paramètres modifiables (table settings)
│   └── audit_service      journal d'audit
├── auth/ dashboard/ candidates/ motivation/ technical/ kobo/ admin/   Blueprints du recrutement
├── portal/                espace candidat (/candidat)
├── campus/                vie du SAS (/campus)
├── infra/                 infrastructures et inventaire (/infrastructures)
├── templates/             Jinja2 (fragments `_*.html` rafraîchis par polling)
└── static/                CSS DFS, JS (polling, chrono, scanner QR), Bootstrap/icônes/jsQR en local
migrations/                Alembic 0001 → 0018
tests/                     pytest (SQLite par défaut, PostgreSQL via TEST_DATABASE_URL)
```

### Règles métier critiques et où elles sont garanties

| Règle | Garantie |
|---|---|
| Un code examen par candidat, un candidat par code | une seule colonne `candidates.exam_code` + contrainte `UNIQUE` |
| Technique seulement après motivation | contrôle service + `CHECK (NOT technical_completed OR motivation_completed)` + routes 404 pour le testeur technique |
| Le testeur technique ne modifie pas les données validées | routes de modification réservées à `admin` / `motivation_tester` |
| Un PC ne sert qu'à un candidat à la fois | `SELECT … FOR UPDATE` sur le poste + **index unique partiel** `(workstation_id) WHERE status IN ('running','paused')` |
| Une session active par candidat | index unique partiel `(candidate_id) WHERE status IN ('running','paused')` |
| Chrono récupérable | horodatages en base ; le navigateur n'affiche que le temps restant calculé par le serveur |
| Matricule unique `AACANDNNNNNN` | attribué à l'insertion (`before_flush`), contrainte `UNIQUE` |
| Une présence par étudiant et par appel | contrainte `UNIQUE (session_id, candidate_id)` |
| Stock jamais négatif | contrôle service + `CHECK (quantity >= 0)` |
| Audit | `audit_logs` écrit dans la **même transaction** que l'action |

Statut global du candidat et heures d'absence : **calculés**, jamais stockés.

« Temps réel » : polling AJAX de fragments HTML (5 s pour le technique, 10 s pour les dashboards et les appels).

### Routes principales

| URL | Rôles |
|---|---|
| `/login`, `/logout`, `/profil` | personnel (profil : nom, téléphone, photo, mot de passe) |
| `/` | dashboard selon le rôle (coach / accueil → `/campus/`, cité → `/infrastructures/`) |
| `/candidates/`, `/candidates/<id>` | rôles du recrutement (testeur technique : éligibles uniquement) |
| `/candidates/new`, `/<id>/edit`, `/<id>/delete`, `/<id>/reset-password` | admin |
| `/candidates/<id>/passed/<motivation\|technical>` | rôles autorisés pour ce test |
| `/motivation/…`, `/technical/…` | admin, superviseur, testeur concerné |
| `/admin/users`, `/admin/rooms`, `/admin/imports`, `/admin/exports`, `/admin/audit`, `/admin/settings`, `/admin/kobo/`, `/admin/scan` | admin |
| `/admin/sas/…` | admin ; cité : classes / chambres / matériel uniquement |
| `/campus/`, `/campus/attendance/…`, `/campus/notifications`, `/campus/exercises/…` | admin, coach (ses classes), accueil |
| `/campus/classes/…` | admin, coach |
| `/campus/visits`, `/campus/students/…` | admin, accueil |
| `/campus/incidents` | personnel du SAS (accueil et admin voient et résolvent tout) |
| `/infrastructures/…` | admin, cité |
| `/candidat/connexion`, `/candidat/inscription` | public |
| `/candidat/`, `/candidat/presence`, `/candidat/classe`, `/candidat/exercices`, `/candidat/notifications`, `/candidat/profil`, `/candidat/mot-de-passe` | candidat (ses propres données uniquement) |
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
docker compose exec web flask seed      # 1 superviseur, 4 testeurs, 2 salles × 10 PC, 20 candidats du jour
```

Les identifiants des comptes de démo sont dans `parametre_sensible.md` (fichier local, non versionné). Avec `FLASK_ENV=production`, la commande exige `--force`.

### Sans Docker (développement)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
export FLASK_APP=wsgi.py FLASK_ENV=development
export DATABASE_URL=postgresql+psycopg://<utilisateur>:<mot_de_passe>@localhost:5432/dfs
export ADMIN_EMAIL=<email_admin> ADMIN_PASSWORD='<mot_de_passe_admin>'
flask db upgrade
flask create-admin
flask run --port 8000
```

## Configuration et variables d'environnement

| Variable | Rôle | Défaut |
|---|---|---|
| `FLASK_ENV` | `production` active cookies `Secure` et exige `SECRET_KEY` | `production` |
| `SECRET_KEY` | signe les sessions, **chiffre le token Kobo et les QR codes** (la changer invalide les QR codes déjà distribués et oblige à ressaisir le token) | — obligatoire |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | administrateur initial (8 caractères min.) | — |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | base (conteneur `db` **et** connexion de l'app) | `dfs` / `dfs` / — |
| `DATABASE_URL` | URL complète, prioritaire sur `POSTGRES_*` | construite depuis `POSTGRES_*` |
| `SESSION_COOKIE_SECURE` | cookies uniquement en HTTPS (`localhost` est accepté par les navigateurs) | `true` en production |
| `KOBO_BASE_URL`, `KOBO_TOKEN`, `KOBO_ASSET_UID` | valeurs par défaut Kobo (l'écran admin est prioritaire) | `https://kf.kobotoolbox.org` |
| `CANDIDATE_DEFAULT_PASSWORD` | mot de passe initial des comptes candidats (changement imposé à la 1re connexion) | voir `parametre_sensible.md` |
| `DEFAULT_TECHNICAL_TEST_DURATION` | durée par défaut en minutes | `60` |
| `TIMEZONE` | fuseau d'affichage (et des dates saisies : appels, exercices) | `Africa/Abidjan` |
| `EXAM_CODE_PREFIX` | préfixe des codes générés | `DFS` |
| `MAX_UPLOAD_MB` | taille max d'un envoi (imports ; les photos sont limitées à 2 Mo) | `10` |
| `WEB_CONCURRENCY` | workers Gunicorn (4 threads chacun) | `3` |
| `WEB_PORT` | port publié sur l'hôte, **uniquement sur 127.0.0.1** (en production, Traefik sert l'app en HTTPS) | `8000` |

Le mot de passe PostgreSQL peut contenir des caractères spéciaux (`@ : / #`) : l'URL est construite et échappée par l'application.

Les paramètres « métier » (campagne, année, préfixe et format du code examen, durée, nom de l'application, fuseau) se modifient dans **Administration → Paramètres**. Format du code : `{prefix}-{year}-{seq:04d}` → `DFS-2026-0001`.

## Base PostgreSQL et migrations

```bash
flask db upgrade                       # appliquer (fait automatiquement au démarrage du conteneur)
flask db migrate -m "description"      # après modification d'un modèle
flask db check                         # vérifie que les modèles et la base sont alignés
```

`entrypoint.sh` exécute `flask db upgrade`, `flask create-admin` puis `flask create-candidate-accounts` avant Gunicorn ; tous sont idempotents.
PostgreSQL n'est **jamais publié** hors du réseau Docker (pas de `ports:` sur `db`).

| Migration | Contenu |
|---|---|
| 0001 – 0003 | schéma initial, rôle superviseur, comptes candidats |
| 0004 – 0005 | cases « Validé » des tests, matricule candidat |
| 0006 – 0010 | SAS, données de validation, classe SAS |
| 0011 – 0012 | photo de profil candidat, appels de présence |
| 0013 – 0014 | rôles coach / accueil / cité, notifications, exercices, registre, problèmes, classes et invitations |
| 0015 – 0017 | profil du personnel, registre des passages (pièce, provenance, activité), heures pointées des appels |
| 0018 | infrastructures, matériel, consommables |

Les migrations 0007 à 0009 sont des **mises à jour de données** (cases « Validé » cochées pour les tests terminés et les admis au SAS) : elles ne s'annulent pas.

## Comptes : administrateur, personnel, profils

L'administrateur initial est créé au démarrage **uniquement si aucun administrateur n'existe**, à partir de `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Aucun identifiant n'est codé en dur.

**Administration → Utilisateurs** :

* onglet **Équipe** : créer / modifier / désactiver le personnel et choisir son rôle ; pour un **coach**, cocher ses classes ;
* onglet **Candidats** : suivre les comptes candidats (matricule, état du compte, dernière connexion), modifier, réinitialiser le mot de passe, désactiver, supprimer.

**Mot de passe oublié** : lien sur les deux pages de connexion (`/mot-de-passe-oublie`). La personne saisit son email (et un moyen de la joindre) ; la réponse est identique que le compte existe ou non. Les demandes apparaissent en tête de **Utilisateurs** (pastille dans le menu) : pour un candidat, *Remettre le mot de passe initial* ; pour le personnel, *Mot de passe temporaire* (affiché une seule fois à l'admin, à transmettre par un canal privé). Dans les deux cas, un nouveau mot de passe est imposé à la connexion suivante. Demandes limitées par IP et par email. L'application n'envoie pas d'emails.

**Mon profil** (`/profil`, clic sur son nom en haut à droite) : chaque membre du personnel modifie son nom, son téléphone, sa **photo** (JPEG / PNG / WebP, 2 Mo max.) et son mot de passe. L'email et le rôle restent gérés par l'admin.

```bash
docker compose exec web flask create-admin
docker compose exec web flask list-users
```

## Espace candidat

* **Comptes créés automatiquement** : chaque candidat chargé reçoit un compte (identifiant = **son email**, mot de passe initial = `CANDIDATE_DEFAULT_PASSWORD`), au démarrage du conteneur et après chaque import. Les candidats **sans compte** (sans email valide, ou email déjà pris) sont listés.
* **Première connexion** : changement de mot de passe obligatoire.
* **Inscription** (`/candidat/inscription`) : refusée si l'email, le téléphone ou la CNI existe déjà.
* **Contenu** : convocation, étapes du recrutement, numéro de pièce, **matricule et QR code**, bandeau « admis au SAS » (classe, chambre, matériel), invitations de classe, notifications, exercices, présence et **heures d'absence**.
* **Profil** (`/candidat/profil`) : téléphone, ville, date de naissance, sexe et **photo** modifiables ; nom, email et CNI restent gérés par la DFS. Le code examen n'est pas affiché au candidat.
* **Support** : fiche candidat → *Réinitialiser le mot de passe*. Supprimer le candidat supprime son compte.

```bash
docker compose exec web flask create-candidate-accounts
```

## Recrutement : imports, validation des tests, matricule et QR code

### Import des candidats

**Imports** → fichier `.csv`, `.xlsx` ou `.xls`.

1. Colonnes reconnues automatiquement (accents, majuscules, espaces ignorés) : `N°`, `Groupe`, `Date`, `Heure`, `Ordre`, `Nom et prénoms`, `Date de naissance`, `Sexe`, `Email`, `Téléphone`, `Ville`, `CNI`. Mapping modifiable.
2. Prévisualisation : lignes valides, erreurs, doublons potentiels (**email → téléphone → CNI → nom + date de naissance**) avec **Ignorer / Mettre à jour / Examiner**.
3. Rien n'est écrit avant **Confirmer l'importation**.

### Validation des tests

* Dans la liste des candidats, une case **« Validé »** par test (motivation, technique), modifiable par les rôles autorisés une fois le test terminé. Elle se coche automatiquement quand le test est terminé ; le jury peut la décocher.
* **Import des résultats** (Imports → *Importer les résultats des tests*, modèle CSV téléchargeable, étape de prévisualisation) : colonnes `email`, `code_examen`, `motivation_status` (oui/non), `technical_status` (oui/non), `salle`, `ordi`, `time` (minutes). Le candidat est retrouvé par **email** ; le code examen lui est attribué s'il n'en a pas ; les tests sont marqués terminés et validés selon le fichier (validation par lot).

### Salles et ordinateurs

**Salles & ordinateurs** : création manuelle, ou **import Excel / CSV** (modèle téléchargeable, colonnes `salle`, `description`, `ordi`, `libelle`). L'import est additif : il crée ce qui manque, ne supprime rien et peut être relancé sans risque.

### Matricule et QR code

* Chaque candidat reçoit un **matricule** unique `AACANDNNNNNN` (AA = année en cours, numéro croissant : `26CAND000001`…).
* Son espace et sa fiche affichent un **QR code** : le matricule, entouré de caractères aléatoires, est **chiffré et signé** (AES-256-GCM, clé dérivée de `SECRET_KEY`). Un QR code modifié ou fabriqué est refusé.
* **Scanner QR** (admin) : caméra (HTTPS ou localhost), photo du QR code, ou douchette → fiche du candidat.

## KoboToolbox

**Administration → KoboToolbox** :

1. URL API, **Asset UID** du formulaire, **token API** (Kobo → Paramètres du compte → Sécurité).
2. Noms des champs du formulaire : CNI, email, téléphone, code candidat, nom complet, statut dossier (nom court ou chemin complet).
3. **Tester la connexion Kobo**, puis **Synchroniser maintenant** (ou `flask sync-kobo`, utilisable dans un cron).

Correspondance : **CNI → Email → Téléphone → Code candidat → Nom complet**. Plusieurs candidats possibles → « Correspondance à vérifier », associée à la main par l'admin (choix conservé). Le token est **chiffré en base** et n'est jamais renvoyé au navigateur.

## SAS : admission, classes, chambres, matériel

**Administration → SAS** :

* Créer un SAS (cohorte), puis **admettre** des candidats par matricules / emails, ou d'un clic tous ceux qui ont validé les deux tests. L'admission passe les deux tests à « Terminé » et « Validé » (code examen généré s'il manque).
* Tableau des admis : **filtres** (recherche, sexe, classe, chambre, matériel), cases « Validé », **heures d'absence**, classe / chambre / matériel modifiables ligne par ligne.
* **Import** des attributions (modèle CSV = les admis actuels, colonnes `matricule, email, nom, classe, chambre, materiel` ; cellule vide = inchangé).
* **Attribution automatique** (ne remplit que les champs vides) : classes équilibrées en effectif et en sexe, chambres non mixtes dans la limite des places, matériel numéroté (`Laptop n°{n}`).
* L'étudiant admis voit « Félicitations, vous êtes admis(e) au SAS » avec sa classe, sa chambre et son matériel.

## Appels de présence et heures d'absence

* **Programmer un appel** (page du SAS ou *Vie du SAS*) : classe (ou tout le SAS), **date et heure**, **heures pointées** (1, 2, 3, 4 ou 7 h) et temps laissé pour marquer.
* Pendant ce temps, l'étudiant marque sa présence en **scannant le QR code projeté** (page « Ma présence » ou appareil photo du téléphone), ou en **présentant son propre QR code** que le personnel scanne. Le QR code projeté change régulièrement : une photo partagée expire vite.
* L'**heure exacte de marquage** est enregistrée. Après le délai, seul le personnel peut encore marquer (scan ou saisie manuelle).
* Celui qui n'a pas marqué compte les **heures pointées en heures d'absence**. Le total apparaît dans l'espace étudiant, sur sa fiche, dans le tableau du SAS et dans l'**export Excel** (feuilles « Synthèse » et « Détail ») ou CSV de la page du SAS.

## Vie du SAS : coachs, accueil, problèmes

* **Classes** (admin, coach) : un coach crée une classe ; elle devient l'une de ses classes. Les étudiants du SAS la rejoignent en **scannant son QR code**, en saisissant son **code ou son adresse**, ou en acceptant une **invitation** (par matricule ou email) qui apparaît sur leur page. « Nouveau code » invalide l'ancien QR code.
* **Notifications** : message à une classe (coach) ou à tout un SAS (admin, accueil), visible dans l'espace étudiant.
* **Exercices** : énoncé en texte enrichi (éditeur **TOAST UI Editor**, embarqué dans `static/vendor/toastui`, contenu stocké en Markdown) avec liens et images (collées ou insérées, enregistrées en base), publication programmée, date limite ; le coach peut modifier le titre, l'énoncé et la date limite. L'étudiant rend une réponse (texte ou lien) ; le coach valide ou demande à refaire (note, commentaire). Une **nouvelle soumission** n'est possible qu'avec l'autorisation du coach (« À refaire » l'accorde), même après la date limite ; **toutes les soumissions sont conservées** avec leur correction.
* **Registre des passages** (accueil) : nom, provenance, type et numéro de pièce, téléphone ; motif **Visite** (candidat ou étudiant visité) ou **Autre activité** (catégorie + activité) ; heure d'arrivée et de départ.
* **Étudiants du SAS** (accueil) : liste filtrable et profil en lecture seule (contact, classe, chambre, présences, problèmes, visites).
* **Problèmes étudiants** : tout le personnel du SAS peut signaler (discipline, santé, logement, matériel, autre) ; l'accueil et l'admin voient tout et résolvent.

## Infrastructures, inventaire et consommables

**Infrastructures** (cité, admin) :

* **Bâtiments → niveaux** (0 = rez-de-chaussée, 1 = 1er étage, -1 = sous-sol) **→ salles** avec nom, usage (chambre, salle de classe, bureau…) et nombre de places. Seuls les éléments vides se suppriment.
* **Inventaire d'une salle** : son matériel et ses consommables, export Excel.
* **Matériel** : n° d'inventaire (auto `INV-00001` ou saisi), catégorie, marque, n° de série, état (en service, en stock, en panne, en réparation, hors service), emplacement, **attribution à un membre du personnel**. Chaque déplacement, attribution ou changement d'état est **historisé**. Liste filtrable et export Excel / CSV.
* **Consommables** (fournitures de bureau, eau, café…) : unité, lieu de stockage, seuil d'alerte ; **entrées / sorties** avec note, historisées ; le stock ne passe jamais sous zéro ; alerte « Stock bas ».

## Docker et Docker Compose

* `Dockerfile` : `python:3.12-slim`, utilisateur non-root `appuser`, `HEALTHCHECK` sur `/health`, Gunicorn `gthread`.
* `docker-compose.yml` : `web` + `db` (`postgres:16-alpine`), volume `pgdata` persistant, volume `uploads`, healthcheck PostgreSQL. Redis n'est pas nécessaire. Les photos sont stockées **en base** (incluses dans les sauvegardes PostgreSQL).

```bash
docker compose up -d --build      # démarrer / mettre à jour
docker compose logs -f web        # journaux
docker compose exec web flask list-users
docker compose down               # arrêter (les données restent dans le volume pgdata)
```

## Déploiement Dokploy

1. **Repository** : poussez ce dossier sur GitHub (`.env` et `parametre_sensible.md` sont ignorés par Git, ne les commitez jamais).
2. Dans Dokploy : **Create Project** → **Create Service** → **Compose**.
3. **Provider** : GitHub, repository et branche ; **Compose Path** : `./docker-compose.yml`.
4. Onglet **Environment** : collez le contenu de votre `.env` de production. Générez `SECRET_KEY` avec `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Gardez `SESSION_COOKIE_SECURE=true`.
5. Onglet **Domains** : **Add Domain** → service `web`, port **8000**, votre domaine, chemin `/`.
6. **HTTPS** : activez le certificat **Let's Encrypt** (indispensable pour la caméra des scanners QR).
7. **Deploy**. Les journaux doivent montrer `Applying database migrations…` puis `Listening at: http://0.0.0.0:8000`.
8. Vérifiez `https://votre-domaine/health` → `{"status": "ok", "database": "connected"}`.

Mises à jour : poussez sur la branche puis **Deploy** ; les migrations s'appliquent seules. En cas de refus du `git push` (« fetch first »), faites `git pull` puis poussez à nouveau.

## Sauvegardes

Les données (photos comprises) vivent dans le volume Docker `pgdata`. Sauvegarde :

```bash
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > dfs_$(date +%F_%H%M).dump
```

Restauration (écrase les données existantes) :

```bash
docker compose stop web
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < dfs_2026-10-02_1800.dump
docker compose start web
```

Conseil : une sauvegarde le matin, à midi et le soir les jours de recrutement (cron sur l'hôte, ou **Backups** de Dokploy). Les exports Excel servent aussi de copie lisible.

## Tests

```bash
pip install -r requirements-dev.txt                      # outils de test (absents de l'image Docker)
pytest                                                   # SQLite en mémoire
TEST_DATABASE_URL=postgresql+psycopg://<utilisateur>:<mot_de_passe>@localhost:5432/dfs_test pytest   # PostgreSQL réel
pip-audit -r requirements.txt                            # vulnérabilités connues des dépendances
```

Couverture (≈ 160 tests) : authentification et rôles (recrutement et SAS), CSRF, imports (candidats, résultats, salles, attributions SAS), motivation, chrono, Kobo, matricule et QR code chiffré (falsification, autre clé, lisibilité), SAS (admission, filtres, attribution automatique), appels (QR tournant, délais, heures d'absence, export), classes et invitations, notifications, exercices, registre des passages, problèmes, infrastructures, inventaire, stock, profils et photos, sécurité (sessions, anti-bruteforce, exports, en-têtes).

Astuce : dans un même test, un seul utilisateur connecté (Flask-Login le garde en cache dans `g` pour tout le contexte d'application).

## Sécurité

* **Secrets** : uniquement dans `.env` et `parametre_sensible.md` (jamais commités : exclus par `.gitignore` et `.dockerignore`). En production, ils vivent dans l'onglet *Environment* de Dokploy. Le dépôt GitHub étant public, ne commitez jamais d'export, de liste de candidats ni de capture contenant des données personnelles.
* **Connexion** : les échecs répétés (par email et par IP) bloquent temporairement les tentatives suivantes ; les inscriptions sont limitées par IP. Seuils : voir `parametre_sensible.md`.
* **Sessions** : expirent automatiquement ; un compte désactivé ou un mot de passe changé déconnecte toutes ses sessions.
* **QR codes** : contenu chiffré et signé (AES-256-GCM) ; changer `SECRET_KEY` invalide tous les QR codes déjà imprimés. Le QR code des appels est signé et change régulièrement.
* **Photos** : format vérifié sur le contenu réel (JPEG, PNG, WebP ; SVG refusé), 2 Mo max., servies sans cache partagé ; chacun ne voit que la sienne côté portail.
* **Navigateur** : Content-Security-Policy (aucun script en ligne), HSTS en HTTPS, caméra autorisée uniquement sur les pages de scan, protection CSRF, cookies `HttpOnly`/`Secure`/`SameSite`, redirections limitées au site.
* **Exports** : les cellules commençant par `=`, `+`, `-`, `@` sont neutralisées (injection de formules Excel).
* **Réseau** : PostgreSQL n'est jamais publié ; l'app n'est publiée que sur `127.0.0.1` (Traefik la sert en HTTPS).
* **Mot de passe initial des candidats** : préférez une valeur propre à la campagne, transmise par un canal privé ; le changement reste imposé à la première connexion.

## Dépannage

| Symptôme | Cause / solution |
|---|---|
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | un autre service utilise 8000 : `WEB_PORT=8010` dans `.env` |
| Connexion impossible en HTTP depuis un autre poste | `SESSION_COOKIE_SECURE=true` exige HTTPS : passez par le domaine HTTPS, ou mettez `false` sur un réseau local de confiance |
| La caméra ne s'ouvre pas pour scanner | la caméra exige HTTPS (ou localhost) : utilisez « Photo du QR code » ou passez par le domaine HTTPS |
| « Signature invalide » au scan d'un QR code candidat | QR code modifié, ou `SECRET_KEY` changée depuis son impression : réimprimez-le depuis la fiche |
| « QR code expiré » au pointage | le QR code projeté a changé : scannez celui actuellement affiché |
| `SECRET_KEY doit être défini en production` | renseignez `SECRET_KEY` dans `.env` |
| `ADMIN_EMAIL et ADMIN_PASSWORD doivent être définis` | variables manquantes au premier démarrage |
| « Session expirée » à l'envoi d'un formulaire | jeton CSRF expiré : rechargez la page |
| Kobo « Token refusé » / « Formulaire introuvable » | vérifiez le token et l'Asset UID ; après changement de `SECRET_KEY`, ressaisissez le token |
| `/health` renvoie 503 | base injoignable : `docker compose ps`, `docker compose logs db` |
| Le chrono semble décalé | l'affichage est recalé sur le serveur ; vérifiez l'heure du serveur (`date` dans le conteneur) |
