from __future__ import annotations

import json
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

NTP_EPOCH_OFFSET = 2208988800


@dataclass
class CityLookupResult:
    name: str
    latitude: float
    longitude: float
    timezone: str
    country: str = ""


@dataclass
class WeatherSnapshot:
    city_name: str
    temperature_c: float
    weather_code: int
    is_day: bool
    sunrise_at: datetime | None
    sunset_at: datetime | None
    display_token: str
    led_mask: int
    summary: str


def fetch_ntp_time(host: str = "pool.ntp.org", timeout: float = 2.0) -> datetime:
    packet = bytearray(48)
    packet[0] = 0x1B
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(timeout)
        client.sendto(packet, (host, 123))
        data, _ = client.recvfrom(48)
    if len(data) < 48:
        raise RuntimeError("NTP response too short")
    seconds = int.from_bytes(data[40:44], "big") - NTP_EPOCH_OFFSET
    return datetime.fromtimestamp(seconds)


def geocode_city(city_name: str, timeout: float = 4.0) -> CityLookupResult:
    params = urlencode(
        {
            "name": city_name,
            "count": 1,
            "language": "zh",
            "format": "json",
        }
    )
    url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
    payload = _fetch_json(url, timeout)
    results = payload.get("results") or []
    if not results:
        raise RuntimeError("City not found")
    first = results[0]
    return CityLookupResult(
        name=first.get("name", city_name),
        latitude=float(first["latitude"]),
        longitude=float(first["longitude"]),
        timezone=first.get("timezone", "Asia/Shanghai"),
        country=first.get("country", ""),
    )


def fetch_weather_snapshot(
    city_name: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    timeout: float = 5.0,
) -> WeatherSnapshot:
    params = urlencode(
        {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "timezone": timezone_name,
            "current": "temperature_2m,weather_code,is_day",
            "daily": "sunrise,sunset",
            "forecast_days": 1,
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    payload = _fetch_json(url, timeout)
    current = payload.get("current") or {}
    daily = payload.get("daily") or {}
    weather_code = int(current.get("weather_code", 0))
    temp_c = float(current.get("temperature_2m", 0.0))
    sunrise_at = _safe_parse_iso((daily.get("sunrise") or [None])[0])
    sunset_at = _safe_parse_iso((daily.get("sunset") or [None])[0])
    summary = weather_code_summary(weather_code)
    return WeatherSnapshot(
        city_name=city_name,
        temperature_c=temp_c,
        weather_code=weather_code,
        is_day=bool(current.get("is_day", 1)),
        sunrise_at=sunrise_at,
        sunset_at=sunset_at,
        display_token=build_weather_token(summary, temp_c),
        led_mask=build_weather_led_mask(weather_code, temp_c),
        summary=summary,
    )


def should_use_day_mode(now: datetime, snapshot: WeatherSnapshot | None) -> bool:
    if snapshot is None or snapshot.sunrise_at is None or snapshot.sunset_at is None:
        return True
    if now < snapshot.sunrise_at:
        return False
    if now >= snapshot.sunset_at:
        return False
    return True


def build_weather_led_mask(weather_code: int, temp_c: float) -> int:
    mask = 0
    if weather_code in {0, 1, 2}:
        mask |= 0x01
    if weather_code in {
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99
    }:
        mask |= 0x02
    if temp_c >= 30.0:
        mask |= 0x04
    return mask


def build_weather_token(summary: str, temp_c: float) -> str:
    label = {
        "SUN": "SUN",
        "CLOUD": "CLD",
        "FOG": "FOG",
        "RAIN": "RAN",
        "SNOW": "SNW",
        "STORM": "STM",
    }.get(summary, "WX_")
    whole = int(round(temp_c))
    if whole >= 0:
        temp_part = f"{whole:02d}"
    else:
        temp_part = f"N{abs(whole) % 10}"
    return f"{label}{temp_part}C".ljust(8, "_")[:8]


def weather_code_summary(weather_code: int) -> str:
    if weather_code in {0, 1}:
        return "SUN"
    if weather_code in {2, 3}:
        return "CLOUD"
    if weather_code in {45, 48}:
        return "FOG"
    if weather_code in {
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82
    }:
        return "RAIN"
    if weather_code in {71, 73, 75, 77, 85, 86}:
        return "SNOW"
    if weather_code in {95, 96, 99}:
        return "STORM"
    return "CLOUD"


def speak_text(text: str) -> subprocess.Popen[bytes]:
    escaped = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 0; "
        f"$s.Speak('{escaped}')"
    )
    return subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code < 500 or attempt == 2:
                raise
        except URLError as exc:
            last_error = exc
            if attempt == 2:
                raise
        time.sleep(0.4 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unexpected fetch_json state")


def _safe_parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
