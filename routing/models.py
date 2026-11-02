from django.db import models


class Place(models.Model):
    """A US city with a representative coordinate, used for offline geocoding.

    Built from `data/us_places.csv` (median of each city's ZIP code centroids).
    """

    name = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    key = models.CharField(max_length=100, help_text="normalize_place_name(name)")
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "state"], name="unique_place_key_state")]

    def __str__(self) -> str:
        return f"{self.name}, {self.state}"


class FuelStation(models.Model):
    """A truck stop from the provided price list, geocoded to its city."""

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=120)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price = models.FloatField(help_text="USD per gallon")
    lat = models.FloatField()
    lon = models.FloatField()

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state})"
