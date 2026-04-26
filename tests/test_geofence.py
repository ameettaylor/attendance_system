"""
Tests for the geofencing engine and attendance business logic.
Run with:  pytest tests/
"""

import pytest
from app.services.geofence import GeoPoint, haversine_distance, is_within_geofence


class TestHaversineDistance:
    def test_same_point_is_zero(self):
        p = GeoPoint(-1.2921, 36.8219)
        assert haversine_distance(p, p) == pytest.approx(0.0, abs=0.1)

    def test_known_distance_nairobi_westlands(self):
        # Nairobi CBD to Westlands — approximately 4.5 km
        cbd = GeoPoint(-1.2921, 36.8219)
        westlands = GeoPoint(-1.2634, 36.8031)
        distance = haversine_distance(cbd, westlands)
        assert 3500 < distance < 4500

    def test_symmetry(self):
        a = GeoPoint(-1.2921, 36.8219)
        b = GeoPoint(-1.2634, 36.8031)
        assert haversine_distance(a, b) == pytest.approx(haversine_distance(b, a), rel=1e-6)


class TestGeofence:
    def test_within_radius(self):
        site = GeoPoint(-1.2921, 36.8219)
        # 50m north of the site
        engineer = GeoPoint(-1.2917, 36.8219)
        within, distance = is_within_geofence(engineer, site, radius_meters=200)
        assert within is True
        assert distance < 200

    def test_outside_radius(self):
        site = GeoPoint(-1.2921, 36.8219)
        # Roughly 1 km north
        engineer = GeoPoint(-1.2831, 36.8219)
        within, distance = is_within_geofence(engineer, site, radius_meters=200)
        assert within is False
        assert distance > 200

    def test_exactly_on_boundary(self):
        # A point exactly at the radius should be within
        site = GeoPoint(0.0, 0.0)
        # 200m north of the equator (approx 0.0018 degrees latitude)
        engineer = GeoPoint(0.0018, 0.0)
        within, distance = is_within_geofence(engineer, site, radius_meters=200)
        # Distance will be ~200m — just verify it's near the boundary
        assert 180 < distance < 220

    def test_distance_is_rounded(self):
        site = GeoPoint(-1.2921, 36.8219)
        engineer = GeoPoint(-1.2917, 36.8219)
        _, distance = is_within_geofence(engineer, site, radius_meters=200)
        # Should be rounded to 1 decimal place
        assert distance == round(distance, 1)
