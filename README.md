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

## Fond de carte

La carte globale utilise le fond sombre CARTO, qui exige désormais une clé gratuite :
demande-la sur https://carto.com/basemaps/apikey puis renseigne `CARTO_API_KEY` dans `.env`.
Sans clé, le site bascule sur le fond sombre Esri, qui n'en demande pas.

Les autres fonds du sélecteur (Topo OpenTopoMap, Plan IGN, Satellite Esri, relief Esri) ne demandent pas de clé.

## Périples

L'onglet « Périples » regroupe automatiquement les activités enchaînées sur plusieurs jours
(voir `activities/journeys.py`). Deux activités sont reliées si la seconde démarre moins de
42 h après la fin de la première, à moins de 5 km de son point d'arrivée, et ailleurs que
le départ précédent (sinon ce sont des boucles depuis chez soi). Les chaînes qui ne
s'éloignent pas d'au moins 10 km, tiennent en une journée ou font des allers-retours entre
deux lieux (domicile-travail) sont écartées. Les seuils sont des constantes en haut du module.

## Structure

- `config/` : réglages du projet Django
- `activities/` : app principale (connexion Strava, activités, visualisations)
- `activities/static/activities/` : feuille de style, icônes, polices Barlow et Leaflet hébergés localement

## Tests

```bash
python manage.py test
```
