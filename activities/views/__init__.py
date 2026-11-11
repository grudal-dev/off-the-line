"""Vues du site, rangées par espace : compte, sorties, Explorer, voyages, affiches, livres."""

from .account import home, import_files, strava_callback, strava_disconnect, strava_login, strava_logout, strava_webhook  # noqa: F401
from .activities import activity_complete, activity_detail, activity_keep, activity_list, activity_remove, activity_restore, activity_sync, activity_sync_auto  # noqa: F401
from .explorer import all_routes_map, create, creations, journey_favorite, suggestion_dismiss  # noqa: F401
from .journeys import journey_complete, journey_detail, journeys_complete  # noqa: F401
from .posters import poster_delete, poster_edit, poster_save, poster_towns  # noqa: F401
from .books import book_delete, book_detail, book_fetch_photos, book_update, book_upload  # noqa: F401
