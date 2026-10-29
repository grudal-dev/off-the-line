"""Décodage des polylignes encodées Google (format utilisé par Strava pour les tracés)."""


def decode(encoded):
    """Renvoie une liste de [lat, lng] à partir d'une polyligne encodée."""
    coords, index, lat, lng = [], 0, 0, 0
    length = len(encoded)
    while index < length:
        for axis in (0, 1):
            shift, result = 0, 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        coords.append([lat / 1e5, lng / 1e5])
    return coords
