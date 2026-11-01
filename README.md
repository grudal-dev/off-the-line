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

## Carte

Deux affichages : « Densité » (les tracés s'additionnent et sont recolorés selon le nombre de
passages : les rues souvent empruntées ressortent) et « Couleurs par sport ».

## Fond de carte

La carte globale utilise le fond sombre CARTO, qui exige désormais une clé gratuite :
demande-la sur https://carto.com/basemaps/apikey puis renseigne `CARTO_API_KEY` dans `.env`.
Sans clé, le site bascule sur le fond sombre Esri, qui n'en demande pas.

Les autres fonds du sélecteur (Topo OpenTopoMap, Plan IGN, Satellite Esri, relief Esri) ne demandent pas de clé.

## Villes sur les cartes

Les cartes affichent quelques grandes villes pour se situer (case « Villes » à côté de « Relief », mémorisée ;
option « Villes sur les cartes » dans la mise en page du livre). Données : Natural Earth, *populated places*
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

### Livre depuis une zone de la carte

Sur la carte, « Sélectionner une zone pour un livre » puis faire glisser pour tracer un rectangle : les
périples qui passent dans la zone sont listés (tous cochés) et « Créer le livre » les envoie à la page Livre.

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
  points de départ et d'arrivée ; les transferts d'un périple sont en pointillés ;
- le **cadre** : filet fin, double filet, cadre épais ou passe-partout, avec sa marge et ses couleurs ;
- les **textes** (en haut, en bas ou aucun) : titre, sous-titre et chiffres proposés, modifiables ;
- le **cadrage** : glisser l'aperçu pour déplacer, molette ou pincement pour zoomer, rotation.

L'aperçu et l'image finale sont dessinés par la même fonction (`static/activities/poster.js`), en millimètres :
ce qu'on voit est ce qui sera imprimé. La résolution est inscrite dans le PNG (bloc `pHYs`) pour que
l'imprimeur retrouve la bonne taille. Les réglages sont mémorisés dans le navigateur. Les fonds de carte
sont chargés en CORS ; si un fournisseur refuse, l'image est quand même produite et un message l'indique.
Les liens « Créer une affiche » des pages périple et activité ouvrent l'affiche sur cette source
(`/affiche/?periple=<id>` ou `?activite=<id>`).

## Structure

- `config/` : réglages du projet Django
- `activities/` : app principale (connexion Strava, activités, visualisations)
- `activities/static/activities/` : feuille de style, icônes, polices Barlow et Leaflet hébergés localement

## Tests

```bash
python manage.py test
```
