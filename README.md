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

## Structure

- `config/` : réglages du projet Django
- `activities/` : app principale (connexion Strava, activités, visualisations)

## Tests

```bash
python manage.py test
```
