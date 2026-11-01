# Off the line

Site Django qui utilise l'API Strava pour afficher les parcours d'un utilisateur sous différentes formes.

## Installation

```bash
git clone https://github.com/grudal-dev/off-the-line.git
cd off-the-line
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # puis remplis les clés Strava
python manage.py migrate
python manage.py runserver
```

Ouvre ensuite http://localhost:8000.

## Configuration Strava

1. Crée une application sur https://www.strava.com/settings/api
2. « Authorization Callback Domain » : `localhost`
3. Copie le Client ID et le Client Secret dans `.env`

### Importer des jetons obtenus hors du site

Si l'autorisation Strava a été faite à la main (navigateur + `curl` / PowerShell sur
`POST https://www.strava.com/oauth/token`), enregistre la réponse JSON dans un fichier puis :

```bash
python manage.py import_strava_tokens strava_papa.json --sync
```

Le compte est créé comme par la connexion du site (utilisateur `strava_<id athlète>`) : l'athlète
pourra ensuite se connecter avec « Se connecter avec Strava » et retrouver ses données. Ré-importer
un fichier met simplement les jetons à jour. Les fichiers UTF-16 écrits par PowerShell sont acceptés,
et `strava_*.json` est ignoré par git.

### Chargement en arrière-plan

Les pages s'affichent sans attendre Strava ni le géocodage. Ce qui manque (photos d'une activité ou d'un
périple, noms de lieux des périples, photos et lieux d'un livre) est récupéré juste après par
`static/activities/background.js`, qui appelle en boucle une adresse `…/completer/` traitant un petit lot à
chaque fois (6 activités, 8 lieux). Un indicateur en bas à gauche montre l'avancement ; à la fin, la page se
recharge à la même position (ou propose un bouton si une saisie est en cours). En cas de quota Strava, nouvel
essai chaque minute ; si le géocodage est indisponible, on s'arrête et on réessaiera à la prochaine visite.

Les scripts et feuilles de style sont inclus avec `{% asset %}` (date de modification en suffixe) pour que le
navigateur ne garde pas une ancienne version après une mise à jour.

### Photos à la demande

Les vidéos Strava, renvoyées avec les photos, sont ignorées (`sync.is_video`).

Dans un livre, les photos manquantes de **toutes** les étapes sont récupérées en arrière-plan : la page
s'affiche tout de suite, appelle `livre/<id>/photos-strava/` par lots de 8 étapes en montrant
l'avancement dans la barre du haut, réessaie chaque minute si Strava limite les requêtes, puis se
recharge (en mode Personnaliser, un bouton « Afficher les nouvelles photos » le propose).

Les photos d'une activité sont récupérées sur Strava la première fois qu'on l'ouvre, qu'on ouvre un
périple qui la contient, ou qu'on crée ou affiche un livre (`sync.ensure_photos`, au plus 20 appels par
page, 60 à la création d'un livre). Elles sont ensuite en base : les affichages suivants ne coûtent
rien. Toute activité jamais vérifiée est interrogée, même si Strava l'annonce sans photo : le compteur
`total_photo_count` de la liste des activités vaut souvent 0 à tort. Si Strava limite les requêtes, la page s'affiche quand même et les photos manquantes arriveront
au prochain affichage. Le bouton « Synchroniser » importe toujours un lot de 40 activités, et la
commande ci-dessous rattrape tout l'historique (utile pour les miniatures de la page Carte).

### Importer toutes les photos

Sur le site, chaque synchronisation n'importe les photos que de 40 activités (quota Strava).
Pour tout récupérer d'un coup :

```bash
python manage.py sync_photos                     # tous les comptes
python manage.py sync_photos --athlete <id>      # un seul athlète
python manage.py sync_photos --all               # aussi les activités annoncées sans photo (long)
```

La commande enchaîne les lots et, quand Strava répond « quota atteint », attend la fenêtre de
15 minutes suivante avant de reprendre. Elle peut être interrompue (Ctrl+C) et relancée sans rien perdre.

## Import de fichiers (sans Strava)

« Importer mes fichiers » (accueil, ou « Importer des fichiers » dans Activités) accepte les fichiers **GPX, FIT
et TCX**, seuls ou dans une archive **ZIP** (aussi `.gz`) : export d'une activité ou de tout l'historique depuis
Garmin Connect, Komoot, Coros, Suunto, Wahoo, ou l'archive de ses données Strava. Une seule zone de dépôt, et
l'aide « Où trouver mes fichiers ? » repliée. Garmin ne peut pas être connecté directement : son programme
développeurs n'accepte plus de nouvelles demandes.

- **Sans compte** : le premier dépôt crée un compte « sans Strava » lié au navigateur (cookie de session d'un an,
  `StravaAccount.is_local`). Pas de déconnexion pour lui (il perdrait ses sorties) ; « Supprimer toutes mes
  données » (Activités → Options avancées) efface tout.
- **Avec Strava** : les sorties importées s'ajoutent à celles de Strava.
- Les activités lues dans un fichier ont un **identifiant négatif** (sous 2^53 pour que le navigateur le lise
  sans arrondi) ; les adresses acceptent ces identifiants (convertisseur `sid`). Elles ont déjà leur profil
  d'altitude et ne déclenchent **aucun appel à Strava** (photos et détails marqués comme récupérés). Une sortie
  déposée deux fois (même départ, même distance) n'est pas dupliquée.
- Lecture : `activities/tracks.py` (distance, D+ avec seuil de 3 m, temps en mouvement, sport, tracé de
  600 points au plus, profil). L'heure locale est celle de Paris en Europe de l'Ouest et centrale, sinon
  déduite de la longitude. Les fichiers FIT sont lus avec `fitdecode`.

Ces fichiers viennent de l'utilisateur lui-même : ils ne relèvent pas du contrat API Strava, ce qui permet de
proposer des affiches et livres payants sans dépendre de l'accord de Strava.

## Carte

Deux affichages : « Densité » (les tracés s'additionnent et sont recolorés selon le nombre de
passages : les rues souvent empruntées ressortent) et « Couleurs par sport ».

## Fond de carte

La carte globale utilise le fond sombre CARTO, qui exige désormais une clé gratuite :
demande-la sur https://carto.com/basemaps/apikey puis renseigne `CARTO_API_KEY` dans `.env`.
Sans clé, le site bascule sur le fond sombre Esri, qui n'en demande pas.

Les autres fonds du sélecteur (Topo OpenTopoMap, Plan IGN, Satellite Esri, relief Esri) ne demandent pas de clé.

### Fonds et vente

Les fonds **Esri** (Satellite, relief ombré, et les fonds de secours Clair/Nuit sans clé CARTO) sont réservés à un
usage personnel : leur revente (affiche ou livre vendus) demande un accord écrit d'Esri. L'affiche le signale
sous le choix du fond. Le fond **Satellite libre** (Sentinel-2 cloudless 2016 d'EOX, 10 m) est sous licence CC BY 4.0 :
il peut être vendu, à condition d'imprimer l'attribution (déjà ajoutée sur l'affiche et dans le livre). Les
millésimes EOX 2018 et suivants, eux, ne sont gratuits que pour un usage non commercial. Dans le livre,
« Personnaliser » permet de choisir l'imagerie (Esri ou Sentinel-2).

## Pays et villes sur les cartes

Les cartes affichent le nom des pays (en vue large à leur position habituelle, sans les micro-États ; en vue rapprochée, le ou les pays de la zone visible, d'après les villes à l'écran) et quelques grandes villes pour se situer (case « Villes » à côté de « Relief », mémorisée ;
option « Villes sur les cartes » dans la mise en page du livre). Données : Natural Earth, *populated places* et *admin 0 countries*
(domaine public), réduites à `activities/static/activities/cities.json` par `scripts/build_cities.py`, avec
les noms en français. `activities/static/activities/cities.js` n'affiche que les villes adaptées au zoom
(zoom minimum conseillé par Natural Earth), des plus importantes aux moins importantes, sans chevauchement,
en évitant les pastilles d'étape, et au plus 18 par carte (6 à 10 dans le livre).

## Périples

L'onglet « Périples » regroupe automatiquement les activités enchaînées sur plusieurs jours
(voir `activities/journeys.py`). Deux activités sont reliées si la seconde démarre moins de
42 h après la fin de la première, à moins de 5 km de son point d'arrivée, et ailleurs que
le départ précédent (sinon ce sont des boucles depuis chez soi). Les chaînes qui ne
s'éloignent pas d'au moins 10 km, s'étalent sur 2 jours ou moins (il faut au moins 3 jours calendaires) ou font des allers-retours entre
deux lieux (domicile-travail) sont écartées. Les seuils sont des constantes en haut du module.

Les voyages discontinus (train, ferry, bus…) sont reconnus : quand l'étape suivante repart loin de
l'arrivée précédente, le saut est accepté comme « transfert » s'il est plausible (vitesse moyenne
≤ 900 km/h, moins de 2 000 km), qu'il ne part ni n'arrive à un lieu d'où partent souvent les
activités (chez soi), qu'il relie des étapes d'un point à un autre, et que le périple compte au
moins autant d'enchaînements continus que de transferts. Le mode de transport se choisit sur la
page du périple ; les transferts sont tracés en pointillés et repris dans le livre.

Les périples sont nommés d'après les communes de départ et d'arrivée (« Annecy → Chamonix-Mont-Blanc »,
« Boucle depuis Annecy »), via le géocodage inverse de la Géoplateforme IGN, ou de Nominatim
(OpenStreetMap) hors de France. Les noms sont mis en cache en base. Le titre et un récit peuvent
être modifiés depuis la page du périple.

### Corriger un périple

Sur la page d'un périple, « Corriger les étapes » permet de :

- **renommer** une étape (le nom est repris sur la carte et dans le livre) ;
- **retirer** une étape détectée à tort, puis la **remettre** si besoin ;
- **ajouter** une sortie que la détection a manquée (activités jusqu'à 3 jours avant ou après) ;
- « **Ce n'est pas un périple** » : masque une fausse détection (liste, carte, nouveaux livres) ;
  les périples masqués restent accessibles en bas de la page Périples pour les rétablir ;
- « Revenir à la détection automatique » : annule toutes les corrections.

Les corrections sont stockées dans `JourneyNote` (`excluded`, `added`, `stage_names`, `hidden`) et appliquées
par-dessus la détection (`journeys.apply_note`). Un périple garde son identifiant (sa 1re activité détectée)
même si on retire cette étape, si bien que son adresse et les livres qui l'utilisent restent valables. Les modes
de transport des transferts sont rattachés à l'étape qui précède le transfert (clé `a<id>`).

### Affiche ou livre depuis une zone de la carte

Sur la carte, « Sélectionner une zone » puis faire glisser pour tracer un rectangle : les activités qui
passent dans la zone (parmi les sports affichés) sont listées, toutes cochées. On peut en décocher,
donner un titre, puis :

- « **Créer une affiche** » ouvre l'Affiche sur ces activités (source « Zone », mémorisée dans le navigateur) ;
- « **Créer un livre** » fabrique un livre d'un seul chapitre avec ces activités dans l'ordre chronologique,
  même si elles ne forment pas un périple (`Book.activity_ids`, `journeys.from_activities`), au plus 150.
  Le titre et le récit de ce chapitre sont enregistrés dans le livre (`selection_title`, `selection_story`).

## Livre

L'onglet « Livre » fabrique un carnet de voyage à partir d'un ou plusieurs périples : couverture,
sommaire, puis pour chaque périple une page d'ouverture (carte et chiffres clés), un carnet de route
(profil d'altitude, récit, tableau des étapes) et une page par étape (carte, profil, description
Strava, photos), avec des pages de photos supplémentaires si besoin. C'est une page HTML au format
A4 : le bouton « Enregistrer en PDF » ouvre l'impression du navigateur (choisir « Enregistrer au
format PDF »).

Les livres sont enregistrés (« Mes livres ») et personnalisables avec « Personnaliser » : titres et
textes modifiables directement sur la page, couverture carte ou photo, sections affichées
(sommaire, carnet de route, profils, dos). Les photos se manipulent directement sur les pages :
✕ pour retirer (la photo retourne dans le bac), ‹ › pour l'ordre, légende en place ; un bac à
droite contient toutes les photos du livre (Strava et ajoutées). On glisse une photo du bac vers
une page, d'une page à une autre (Alt : copie), ou de l'ordinateur vers une page ; Ctrl+V colle
une image sur l'étape sélectionnée. Une même photo peut servir sur plusieurs étapes. Les photos ne sont jamais recadrées : leur nombre par page et leur
disposition dépendent de leurs proportions (`book.best_layout`). Les cartes du livre sont
uniquement en imagerie satellite (Esri World Imagery, attribution imprimée).

Les photos ajoutées sont stockées dans `media/` (`DJANGO_MEDIA_ROOT`). Django ne les sert qu'en
mode DEBUG : en production, il faut les servir par le serveur web ou un stockage externe. Descriptions et profils d'altitude sont récupérés sur Strava à la première génération
(deux requêtes par étape) puis mis en cache.

## Affiche

L'onglet « Affiche » compose une image à imprimer (PNG haute résolution) à partir de toutes les sorties
(filtrées par sport et par dates, raccourcis par année), d'un périple ou d'une activité. On choisit :

- le **format** (A4 à A1, 30 × 40, 40 × 50, 50 × 70, carré 30 × 30), le sens et la résolution (150, 200 ou 300 ppp) ;
- le **style** : thèmes prêts à l'emploi, fond uni ou fond de carte (relief en option), couleurs du fond et du texte ;
- les **tracés** : une couleur, par sport, dégradé dans le temps ou densité ; épaisseur (en mm), opacité, halo,
  liseré (couleur du fond), points de départ et d'arrivée, point à chaque fin d'étape ; les transferts d'un
  périple sont en pointillés ;
- le **fond atténué** : avec un fond de carte, couleurs adoucies et voile de la couleur du fond (35 % par défaut)
  pour que le tracé ressorte ;
- les **repères** : frontières des pays, régions et départements, côtes (trait, épaisseur, opacité, couleur) et
  grandes villes (nombre, taille, couleur, majuscules, point, contour), placées sans chevauchement ;
- les **périples en évidence** (avec « Mes sorties ») : couleur, épaisseur, liseré, noms, choix des périples ;
- la **recherche** dans les noms des activités (« Mes sorties », et liste de « Une activité ») : sans accents
  ni majuscules, tous les mots doivent y figurer, et elle se combine aux sports et aux dates ;
- le **cadre** : filet fin, double filet, cadre épais ou passe-partout, avec sa marge et ses couleurs ;
- la **marque Off the line**, reprise du logo (la ligne droite et le parcours qui en sort) : « Coupure » (dans
  le filet du bas), « Échappée » (le cadre quitte sa ligne, par défaut), « Signature » (dans la marge, avec
  le format et l'année), « Tampon » (sceau dans le coin), ou aucune ;
- les **textes** (en haut, en bas ou aucun) : titre, sous-titre et chiffres proposés, modifiables ;
- le **cadrage** : glisser l'aperçu pour déplacer, molette ou pincement pour zoomer, rotation, air autour du
  tracé ; un conseil propose l'autre orientation quand elle remplirait nettement mieux l'affiche ;
- les **villes** : les plus grandes de la zone, ou les **villes-étapes** d'un périple (départ et arrivée en
  évidence, puis des étapes réparties le long du parcours, d'après les noms de lieux déjà connus) ;
- le **profil d'altitude** dans le bandeau (périple, zone, activité) : altitudes enchaînées si toutes les
  étapes en ont un (récupérés à la création d'un livre), sinon le D+ de chaque étape en barres.

Option « **Pays traversés, façon atlas** » (périple, zone, activité) : les pays traversés sont légèrement
éclaircis et leur nom (en français, d'après Natural Earth) est écrit en grandes capitales espacées et
discrètes, à sa place habituelle ou, si elle est hors cadre ou occupée, à côté du parcours sans le couvrir.

Les chiffres proposés comptent les **jours** et les **pays traversés** (au moins deux), d'après les contours
simplifiés de Natural Earth 1:110 M (`countries-shapes.json`, généré par `scripts/build_countries.py`) : un pays
ne compte qu'au-delà de 15 km parcourus, sans compter les points qui ont un autre pays à moins de 8 km (ces
contours se trompent de quelques kilomètres près des frontières ; la mer ne gêne pas). Les polices Barlow n'ayant ni l'espace fine du format français des
nombres ni la flèche « → », la première est remplacée par une espace insécable et la seconde est dessinée.

L'aperçu et l'image finale sont dessinés par la même fonction (`static/activities/poster.js`), en millimètres :
ce qu'on voit est ce qui sera imprimé. La résolution est inscrite dans le PNG (bloc `pHYs`) pour que
l'imprimeur retrouve la bonne taille. Les réglages sont mémorisés dans le navigateur. Les fonds de carte
sont chargés en CORS ; si un fournisseur refuse, l'image est quand même produite et un message l'indique.
Frontières et côtes viennent de Natural Earth (domaine public), réduites à
`activities/static/activities/borders-*.json` par `scripts/build_borders.py` ; ces fichiers ne sont chargés
que si l'option est cochée. Les villes réutilisent `cities.json`.
Les liens « Créer une affiche » des pages périple et activité ouvrent l'affiche sur cette source
(`/affiche/?periple=<id>` ou `?activite=<id>`).

## Mise en ligne (Railway)

Le dépôt est prêt pour [Railway](https://railway.com) (`railway.json`) : Gunicorn sert le site,
WhiteNoise les fichiers statiques, et SQLite + les photos des livres vivent sur un disque persistant.
Au démarrage, `migrate` et `collectstatic` sont lancés automatiquement.

1. Sur Railway : **New Project → Deploy from GitHub repo** → `off-the-line` (plan Hobby).
2. Dans le service : **Settings → Networking → Generate Domain** (ex. `off-the-line.up.railway.app`).
3. Clic droit sur le service → **Attach Volume**, point de montage `/data`.
4. Onglet **Variables** :

   | Variable | Valeur |
   |---|---|
   | `DJANGO_DEBUG` | `False` |
   | `DJANGO_SECRET_KEY` | une longue chaîne aléatoire (`python -c "import secrets; print(secrets.token_urlsafe(50))"`) |
   | `DJANGO_ALLOWED_HOSTS` | `off-the-line.up.railway.app` |
   | `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://off-the-line.up.railway.app` |
   | `DJANGO_DB_PATH` | `/data/db.sqlite3` |
   | `DJANGO_MEDIA_ROOT` | `/data/media` |
   | `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` | ceux de https://www.strava.com/settings/api |
   | `STRAVA_REDIRECT_URI` | `https://off-the-line.up.railway.app/strava/callback/` |
   | `CARTO_API_KEY` | facultatif |

5. Sur https://www.strava.com/settings/api : **Authorization Callback Domain** = `off-the-line.up.railway.app`
   (sans `https://`), et passer l'application à 10 athlètes.

Les comptes existants peuvent être recréés simplement en se reconnectant avec Strava sur le site en ligne.

## Mise en ligne (Render)

`render.yaml` décrit tout le service : sur [Render](https://render.com), **New → Blueprint**, choisir le
dépôt `off-the-line`, puis renseigner `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` (et `CARTO_API_KEY` si
besoin) quand Render les demande. La clé secrète Django est générée, le disque persistant `/data` (base +
photos) est créé, et le domaine `xxx.onrender.com` est ajouté automatiquement à `ALLOWED_HOSTS`, aux
origines CSRF et à l'adresse de retour Strava. Plan Starter (≈ 7 $/mois + disque) : le plan gratuit
n'accepte pas de disque.

Il reste à mettre le domaine `xxx.onrender.com` dans **Authorization Callback Domain** sur
https://www.strava.com/settings/api. Sur Railway, le domaine généré est lui aussi pris en compte automatiquement
(`RAILWAY_PUBLIC_DOMAIN`) : `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` et `STRAVA_REDIRECT_URI` deviennent facultatifs.

## Structure

- `config/` : réglages du projet Django
- `activities/` : app principale (connexion Strava, activités, visualisations)
- `activities/static/activities/` : feuille de style, icônes, polices Barlow et Leaflet hébergés localement

## Tests

```bash
python manage.py test
```
