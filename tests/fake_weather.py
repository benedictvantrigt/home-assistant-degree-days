"""Fake Open-Meteo and KNMI EDR servers for the HTTP client mock."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

TZ = ZoneInfo("Europe/Amsterdam")
DE_BILT = "0-20000-0-06260"
SCHIPHOL = "0-20000-0-06240"


def final_temperature(day: date) -> float:
    """Temperature of the "final" data: alternating 8 and 13 °C."""
    return 8.0 if day.toordinal() % 2 == 0 else 13.0


RECENT_TEMPERATURE = 10.0


@dataclass
class FakeOpenMeteo:
    """Archive with final values until ``final_until``; forecast values 10 °C."""

    final_until: date
    now: datetime
    archive_requests: list[tuple[date, date]] = field(default_factory=list)
    forecast_requests: int = 0

    def register(self, aioclient_mock: AiohttpClientMocker) -> None:
        """Register the fake endpoints."""
        aioclient_mock.get(
            "https://archive-api.open-meteo.com/v1/archive", side_effect=self._archive
        )
        aioclient_mock.get(
            "https://api.open-meteo.com/v1/forecast", side_effect=self._forecast
        )

    async def _archive(
        self, method: str, url: URL, data: object
    ) -> AiohttpClientMockResponse:
        assert url.query["daily"] == "temperature_2m_mean"
        assert url.query["models"] == "era5_land"
        start = date.fromisoformat(url.query["start_date"])
        end = date.fromisoformat(url.query["end_date"])
        self.archive_requests.append((start, end))
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "daily": {
                    "time": [d.isoformat() for d in days],
                    "temperature_2m_mean": [
                        final_temperature(d) if d <= self.final_until else None
                        for d in days
                    ],
                }
            },
        )

    async def _forecast(
        self, method: str, url: URL, data: object
    ) -> AiohttpClientMockResponse:
        self.forecast_requests += 1
        past_days = int(url.query["past_days"])
        local_today = self.now.astimezone(TZ).date()
        first = datetime.combine(
            local_today - timedelta(days=past_days), datetime.min.time()
        )
        hours = [first + timedelta(hours=h) for h in range((past_days + 1) * 24)]
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "hourly": {
                    "time": [h.strftime("%Y-%m-%dT%H:%M") for h in hours],
                    "temperature_2m": [RECENT_TEMPERATURE for _ in hours],
                }
            },
        )


@dataclass
class FakeKnmi:
    """EDR API with validated daily TG until ``validated_until``; 10-minute ta 10 °C."""

    validated_until: date
    now: datetime
    refuse: bool = False
    daily_requests: list[str] = field(default_factory=list)
    ten_minute_requests: list[str] = field(default_factory=list)

    base = "https://api.dataplatform.knmi.nl/edr/v1"
    daily = "daily-in-situ-meteorological-observations-validated"
    ten_minute = "10-minute-in-situ-meteorological-observations"

    def register(self, aioclient_mock: AiohttpClientMocker) -> None:
        """Register the fake endpoints."""
        aioclient_mock.get(
            re.compile(re.escape(self.base) + ".*"), side_effect=self._handle
        )

    async def _handle(
        self, method: str, url: URL, data: object
    ) -> AiohttpClientMockResponse:
        if self.refuse:
            return AiohttpClientMockResponse(method, url, status=403, text="forbidden")
        path = url.path.removeprefix("/edr/v1")
        if path == "/collections":
            return AiohttpClientMockResponse(
                method,
                url,
                json={
                    "collections": [
                        {"id": self.daily, "parameter_names": {"TG": {}, "TN": {}}},
                        {
                            "id": self.ten_minute,
                            "parameter_names": {"ta": {}, "rh": {}},
                        },
                    ]
                },
            )
        if path == f"/collections/{self.daily}/locations":
            return AiohttpClientMockResponse(
                method,
                url,
                json={
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": SCHIPHOL,
                            "geometry": {"type": "Point", "coordinates": [4.79, 52.32]},
                            "properties": {"name": "Schiphol"},
                        },
                        {
                            "type": "Feature",
                            "id": DE_BILT,
                            "geometry": {"type": "Point", "coordinates": [5.18, 52.10]},
                            "properties": {"name": "De Bilt"},
                        },
                    ],
                },
            )
        start_text, end_text = url.query["datetime"].split("/")
        start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
        station = path.rsplit("/", 1)[-1]
        if station not in (DE_BILT, SCHIPHOL):
            return AiohttpClientMockResponse(method, url, status=404, text="unknown")
        if path == f"/collections/{self.daily}/locations/{station}":
            assert url.query["parameter-name"] == "TG"
            self.daily_requests.append(url.query["datetime"])
            # A daily value is stamped with the end of its UTC day.
            times, values = [], []
            moment = start + timedelta(days=1)
            while moment <= end:
                day = (moment - timedelta(days=1)).date()
                if day <= self.validated_until:
                    times.append(moment)
                    values.append(final_temperature(day))
                moment += timedelta(days=1)
            return self._coverage(method, url, "TG", times, values)
        if path == f"/collections/{self.ten_minute}/locations/{station}":
            assert url.query["parameter-name"] == "ta"
            self.ten_minute_requests.append(url.query["datetime"])
            times, values = [], []
            moment = start + timedelta(minutes=10)
            while moment <= min(end, self.now):
                times.append(moment)
                values.append(RECENT_TEMPERATURE)
                moment += timedelta(minutes=10)
            return self._coverage(method, url, "ta", times, values)
        return AiohttpClientMockResponse(method, url, status=404, text="not found")

    @staticmethod
    def _coverage(method, url, parameter, times, values) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "type": "CoverageCollection",
                "coverages": [
                    {
                        "type": "Coverage",
                        "domain": {
                            "type": "Domain",
                            "domainType": "PointSeries",
                            "axes": {
                                "x": {"values": [5.18]},
                                "y": {"values": [52.1]},
                                "t": {
                                    "values": [
                                        t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                                        for t in times
                                    ]
                                },
                            },
                        },
                        "ranges": {
                            parameter: {
                                "type": "NdArray",
                                "dataType": "float",
                                "axisNames": ["t"],
                                "shape": [len(values)],
                                "values": values,
                            }
                        },
                    }
                ],
            },
        )
