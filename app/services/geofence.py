"""
Geofencing utilities.

Uses the Haversine formula to calculate the great-circle distance between
two GPS coordinates.  No external API required — runs entirely offline.
"""

import math
from dataclasses import dataclass

EARTH_RADIUS_METERS = 6_371_000


@dataclass
class GeoPoint:
    latitude: float
    longitude: float


def haversine_distance(point_a: GeoPoint, point_b: GeoPoint) -> float:
    """
    Return the distance in metres between two GPS coordinates.
    Accurate to within ~0.5% for distances up to a few hundred kilometres.
    """
    lat1, lon1 = math.radians(point_a.latitude), math.radians(point_a.longitude)
    lat2, lon2 = math.radians(point_b.latitude), math.radians(point_b.longitude)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_METERS * c


def is_within_geofence(
    engineer_location: GeoPoint,
    site_location: GeoPoint,
    radius_meters: float,
) -> tuple[bool, float]:
    """
    Check whether an engineer is within the allowed radius of a site.

    Returns:
        (within_geofence, distance_meters)
    """
    distance = haversine_distance(engineer_location, site_location)
    return distance <= radius_meters, round(distance, 1)
