from __future__ import annotations

import json
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NTP_EPOCH_OFFSET = 2208988800

DEFAULT_UTC_OFFSET_SECONDS = 8 * 3600
KNOWN_TIMEZONE_OFFSETS = {
    "Asia/Shanghai": 8 * 3600,
    "Asia/Chongqing": 8 * 3600,
    "Asia/Hong_Kong": 8 * 3600,
    "Asia/Singapore": 8 * 3600,
    "Asia/Tokyo": 9 * 3600,
    "Asia/Seoul": 9 * 3600,
    "Asia/Bangkok": 7 * 3600,
    "Europe/London": 0,
    "Europe/Berlin": 1 * 3600,
    "Europe/Paris": 1 * 3600,
    "America/New_York": -5 * 3600,
    "America/Los_Angeles": -8 * 3600,
    "Australia/Sydney": 10 * 3600,
}
CITY_PRESETS = {
    "上海": (31.2304, 121.4737, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "中国"),
    "shanghai": (31.2304, 121.4737, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "China"),
    "北京": (39.9042, 116.4074, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "中国"),
    "beijing": (39.9042, 116.4074, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "China"),
    "成都": (30.5728, 104.0668, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "中国"),
    "chengdu": (30.5728, 104.0668, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "China"),
    "东京": (35.6764, 139.6500, "Asia/Tokyo", 9 * 3600, "日本"),
    "tokyo": (35.6764, 139.6500, "Asia/Tokyo", 9 * 3600, "Japan"),
    "伦敦": (51.5072, -0.1276, "Europe/London", 0, "英国"),
    "london": (51.5072, -0.1276, "Europe/London", 0, "United Kingdom"),
    "纽约": (40.7128, -74.0060, "America/New_York", -5 * 3600, "美国"),
    "new york": (40.7128, -74.0060, "America/New_York", -5 * 3600, "USA"),
}


@dataclass
class CityLookupResult:
    name: str
    latitude: float
    longitude: float
    timezone: str
    utc_offset_seconds: int = DEFAULT_UTC_OFFSET_SECONDS
    country: str = ""


@dataclass
class WeatherSnapshot:
    city_name: str
    temperature_c: float
    weather_code: int
    is_day: bool
    utc_offset_seconds: int
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
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def timezone_now(
    timezone_name: str,
    utc_moment: datetime | None = None,
    fallback_offset_seconds: int | None = None,
) -> datetime:
    moment = utc_moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return moment.astimezone(ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, ModuleNotFoundError, ValueError):
        offset_seconds = (
            fallback_offset_seconds
            if fallback_offset_seconds is not None
            else infer_timezone_offset_seconds(timezone_name)
        )
        fallback_tz = timezone(
            timedelta(seconds=offset_seconds),
            name=timezone_name,
        )
        return moment.astimezone(fallback_tz)


def infer_timezone_offset_seconds(
    timezone_name: str,
    default: int = DEFAULT_UTC_OFFSET_SECONDS,
) -> int:
    if timezone_name in KNOWN_TIMEZONE_OFFSETS:
        return KNOWN_TIMEZONE_OFFSETS[timezone_name]
    if timezone_name.startswith("Etc/GMT"):
        suffix = timezone_name.removeprefix("Etc/GMT")
        if suffix:
            try:
                parsed = int(suffix)
            except ValueError:
                return default
            return -parsed * 3600
    return default


def geocode_city(city_name: str, timeout: float = 4.0) -> CityLookupResult:
    normalized = city_name.strip()
    preset = CITY_PRESETS.get(normalized.lower()) or CITY_PRESETS.get(normalized)
    if preset is not None:
        latitude, longitude, timezone_name, offset_seconds, country = preset
        return CityLookupResult(
            name=normalized,
            latitude=latitude,
            longitude=longitude,
            timezone=timezone_name,
            utc_offset_seconds=offset_seconds,
            country=country,
        )
    result = _geocode_open_meteo(normalized, timeout)
    if result is not None:
        return result
    result = _geocode_nominatim(normalized, timeout)
    if result is not None:
        return result
    raise RuntimeError("City not found")


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
    utc_offset_seconds = int(
        payload.get(
            "utc_offset_seconds",
            infer_timezone_offset_seconds(timezone_name),
        )
    )
    sunrise_at = _safe_parse_iso((daily.get("sunrise") or [None])[0])
    sunset_at = _safe_parse_iso((daily.get("sunset") or [None])[0])
    summary = weather_code_summary(weather_code)
    return WeatherSnapshot(
        city_name=city_name,
        temperature_c=temp_c,
        weather_code=weather_code,
        is_day=bool(current.get("is_day", 1)),
        utc_offset_seconds=utc_offset_seconds,
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


def weather_emoji(weather_code: int) -> str:
    summary = weather_code_summary(weather_code)
    return {
        "SUN": "☀",
        "CLOUD": "☁",
        "FOG": "🌫",
        "RAIN": "🌧",
        "SNOW": "❄",
        "STORM": "⛈",
    }.get(summary, "☁")


def format_weather_summary(weather_code: int, temp_c: float) -> str:
    return f"{weather_emoji(weather_code)} {weather_code_summary(weather_code)} {temp_c:.1f}C"


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
    request = Request(
        url,
        headers={
            "User-Agent": "SmartClockHost/2.0 (+https://github.com/Cyh29hao)",
            "Accept": "application/json",
        },
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
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


def _geocode_open_meteo(city_name: str, timeout: float) -> CityLookupResult | None:
    params = urlencode(
        {
            "name": city_name,
            "count": 1,
            "language": "zh",
            "format": "json",
        }
    )
    url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
    try:
        payload = _fetch_json(url, timeout)
    except Exception:
        return None
    results = payload.get("results") or []
    if not results:
        return None
    first = results[0]
    timezone_name = first.get("timezone", "Asia/Shanghai")
    return CityLookupResult(
        name=first.get("name", city_name),
        latitude=float(first["latitude"]),
        longitude=float(first["longitude"]),
        timezone=timezone_name,
        utc_offset_seconds=infer_timezone_offset_seconds(timezone_name),
        country=first.get("country", ""),
    )


def _geocode_nominatim(city_name: str, timeout: float) -> CityLookupResult | None:
    params = urlencode(
        {
            "q": city_name,
            "format": "jsonv2",
            "limit": 1,
            "accept-language": "zh-CN,zh,en",
        }
    )
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    try:
        payload = _fetch_json(url, timeout)
    except Exception:
        return None
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    lat = float(first["lat"])
    lon = float(first["lon"])
    display_name = str(first.get("display_name", city_name)).split(",")[0].strip() or city_name
    timezone_name = _infer_timezone_from_coordinates(display_name, lat, lon)
    return CityLookupResult(
        name=display_name,
        latitude=lat,
        longitude=lon,
        timezone=timezone_name,
        utc_offset_seconds=infer_timezone_offset_seconds(timezone_name),
        country="",
    )


def _infer_timezone_from_coordinates(city_name: str, latitude: float, longitude: float) -> str:
    preset = CITY_PRESETS.get(city_name.lower()) or CITY_PRESETS.get(city_name)
    if preset is not None:
        return preset[2]
    if 73 <= longitude <= 135 and 18 <= latitude <= 54:
        return "Asia/Shanghai"
    if 126 <= longitude <= 146 and 30 <= latitude <= 46:
        return "Asia/Tokyo"
    if -10 <= longitude <= 3 and 49 <= latitude <= 61:
        return "Europe/London"
    if -130 <= longitude <= -60 and 24 <= latitude <= 50:
        return "America/New_York"
    return "Asia/Shanghai"
