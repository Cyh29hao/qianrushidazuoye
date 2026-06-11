from __future__ import annotations

import json
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, Request, build_opener, getproxies, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NTP_EPOCH_OFFSET = 2208988800
DEFAULT_NTP_HOSTS = [
    "ntp.aliyun.com",
    "time1.cloud.tencent.com",
]
HTTP_TIME_URLS = [
    "https://www.baidu.com/",
]
LOCAL_HTTP_PROXY_PORTS = (7890, 7897, 10809, 10808, 8080)

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
    "America/Detroit": -5 * 3600,
    "America/Toronto": -5 * 3600,
    "America/Los_Angeles": -8 * 3600,
    "America/Vancouver": -8 * 3600,
    "America/Chicago": -6 * 3600,
    "America/Mexico_City": -6 * 3600,
    "America/Sao_Paulo": -3 * 3600,
    "America/Denver": -7 * 3600,
    "America/Phoenix": -7 * 3600,
    "Australia/Sydney": 10 * 3600,
    "Australia/Melbourne": 10 * 3600,
    "Australia/Brisbane": 10 * 3600,
    "Australia/Adelaide": 9 * 3600 + 1800,
    "Australia/Perth": 8 * 3600,
    "Asia/Dubai": 4 * 3600,
    "Asia/Kolkata": 5 * 3600 + 1800,
    "Asia/Jakarta": 7 * 3600,
    "Asia/Manila": 8 * 3600,
    "Asia/Kuala_Lumpur": 8 * 3600,
    "Asia/Taipei": 8 * 3600,
    "Asia/Jerusalem": 2 * 3600,
    "Asia/Riyadh": 3 * 3600,
    "Europe/Moscow": 3 * 3600,
    "Europe/Rome": 1 * 3600,
    "Europe/Madrid": 1 * 3600,
    "Europe/Amsterdam": 1 * 3600,
    "Europe/Zurich": 1 * 3600,
    "Africa/Cairo": 2 * 3600,
    "Africa/Johannesburg": 2 * 3600,
}
CITY_PRESETS = {
    "上海": (31.2304, 121.4737, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "中国"),
    "shanghai": (31.2304, 121.4737, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "China"),
    "北京": (39.9042, 116.4074, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "中国"),
    "beijing": (39.9042, 116.4074, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "China"),
    "杭州": (30.2741, 120.1551, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "中国"),
    "hangzhou": (30.2741, 120.1551, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "China"),
    "成都": (30.5728, 104.0668, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "中国"),
    "chengdu": (30.5728, 104.0668, "Asia/Shanghai", DEFAULT_UTC_OFFSET_SECONDS, "China"),
    "东京": (35.6764, 139.6500, "Asia/Tokyo", 9 * 3600, "日本"),
    "tokyo": (35.6764, 139.6500, "Asia/Tokyo", 9 * 3600, "Japan"),
    "伦敦": (51.5072, -0.1276, "Europe/London", 0, "英国"),
    "london": (51.5072, -0.1276, "Europe/London", 0, "United Kingdom"),
    "纽约": (40.7128, -74.0060, "America/New_York", -5 * 3600, "美国"),
    "new york": (40.7128, -74.0060, "America/New_York", -5 * 3600, "USA"),
    "nyc": (40.7128, -74.0060, "America/New_York", -5 * 3600, "USA"),
    "休斯顿": (29.7604, -95.3698, "America/Chicago", -6 * 3600, "美国"),
    "houston": (29.7604, -95.3698, "America/Chicago", -6 * 3600, "USA"),
    "芝加哥": (41.8781, -87.6298, "America/Chicago", -6 * 3600, "美国"),
    "chicago": (41.8781, -87.6298, "America/Chicago", -6 * 3600, "USA"),
    "洛杉矶": (34.0522, -118.2437, "America/Los_Angeles", -8 * 3600, "美国"),
    "los angeles": (34.0522, -118.2437, "America/Los_Angeles", -8 * 3600, "USA"),
    "la": (34.0522, -118.2437, "America/Los_Angeles", -8 * 3600, "USA"),
    "华盛顿": (38.9072, -77.0369, "America/New_York", -5 * 3600, "美国"),
    "washington": (38.9072, -77.0369, "America/New_York", -5 * 3600, "USA"),
    "washington dc": (38.9072, -77.0369, "America/New_York", -5 * 3600, "USA"),
    "旧金山": (37.7749, -122.4194, "America/Los_Angeles", -8 * 3600, "美国"),
    "san francisco": (37.7749, -122.4194, "America/Los_Angeles", -8 * 3600, "USA"),
    "西雅图": (47.6062, -122.3321, "America/Los_Angeles", -8 * 3600, "美国"),
    "seattle": (47.6062, -122.3321, "America/Los_Angeles", -8 * 3600, "USA"),
    "多伦多": (43.6532, -79.3832, "America/Toronto", -5 * 3600, "加拿大"),
    "toronto": (43.6532, -79.3832, "America/Toronto", -5 * 3600, "Canada"),
    "温哥华": (49.2827, -123.1207, "America/Vancouver", -8 * 3600, "加拿大"),
    "vancouver": (49.2827, -123.1207, "America/Vancouver", -8 * 3600, "Canada"),
    "渥太华": (45.4215, -75.6972, "America/Toronto", -5 * 3600, "加拿大"),
    "ottawa": (45.4215, -75.6972, "America/Toronto", -5 * 3600, "Canada"),
    "巴黎": (48.8566, 2.3522, "Europe/Paris", 1 * 3600, "法国"),
    "paris": (48.8566, 2.3522, "Europe/Paris", 1 * 3600, "France"),
    "柏林": (52.5200, 13.4050, "Europe/Berlin", 1 * 3600, "德国"),
    "berlin": (52.5200, 13.4050, "Europe/Berlin", 1 * 3600, "Germany"),
    "罗马": (41.9028, 12.4964, "Europe/Rome", 1 * 3600, "意大利"),
    "rome": (41.9028, 12.4964, "Europe/Rome", 1 * 3600, "Italy"),
    "马德里": (40.4168, -3.7038, "Europe/Madrid", 1 * 3600, "西班牙"),
    "madrid": (40.4168, -3.7038, "Europe/Madrid", 1 * 3600, "Spain"),
    "莫斯科": (55.7558, 37.6173, "Europe/Moscow", 3 * 3600, "俄罗斯"),
    "moscow": (55.7558, 37.6173, "Europe/Moscow", 3 * 3600, "Russia"),
    "新加坡": (1.3521, 103.8198, "Asia/Singapore", 8 * 3600, "新加坡"),
    "singapore": (1.3521, 103.8198, "Asia/Singapore", 8 * 3600, "Singapore"),
    "首尔": (37.5665, 126.9780, "Asia/Seoul", 9 * 3600, "韩国"),
    "seoul": (37.5665, 126.9780, "Asia/Seoul", 9 * 3600, "South Korea"),
    "曼谷": (13.7563, 100.5018, "Asia/Bangkok", 7 * 3600, "泰国"),
    "bangkok": (13.7563, 100.5018, "Asia/Bangkok", 7 * 3600, "Thailand"),
    "迪拜": (25.2048, 55.2708, "Asia/Dubai", 4 * 3600, "阿联酋"),
    "dubai": (25.2048, 55.2708, "Asia/Dubai", 4 * 3600, "United Arab Emirates"),
    "新德里": (28.6139, 77.2090, "Asia/Kolkata", 5 * 3600 + 1800, "印度"),
    "delhi": (28.6139, 77.2090, "Asia/Kolkata", 5 * 3600 + 1800, "India"),
    "new delhi": (28.6139, 77.2090, "Asia/Kolkata", 5 * 3600 + 1800, "India"),
    "孟买": (19.0760, 72.8777, "Asia/Kolkata", 5 * 3600 + 1800, "印度"),
    "mumbai": (19.0760, 72.8777, "Asia/Kolkata", 5 * 3600 + 1800, "India"),
    "悉尼": (-33.8688, 151.2093, "Australia/Sydney", 10 * 3600, "澳大利亚"),
    "sydney": (-33.8688, 151.2093, "Australia/Sydney", 10 * 3600, "Australia"),
    "堪培拉": (-35.2809, 149.1300, "Australia/Sydney", 10 * 3600, "澳大利亚"),
    "canberra": (-35.2809, 149.1300, "Australia/Sydney", 10 * 3600, "Australia"),
    "墨西哥城": (19.4326, -99.1332, "America/Mexico_City", -6 * 3600, "墨西哥"),
    "mexico city": (19.4326, -99.1332, "America/Mexico_City", -6 * 3600, "Mexico"),
    "圣保罗": (-23.5558, -46.6396, "America/Sao_Paulo", -3 * 3600, "巴西"),
    "sao paulo": (-23.5558, -46.6396, "America/Sao_Paulo", -3 * 3600, "Brazil"),
    "巴西利亚": (-15.7939, -47.8828, "America/Sao_Paulo", -3 * 3600, "巴西"),
    "brasilia": (-15.7939, -47.8828, "America/Sao_Paulo", -3 * 3600, "Brazil"),
    "开罗": (30.0444, 31.2357, "Africa/Cairo", 2 * 3600, "埃及"),
    "cairo": (30.0444, 31.2357, "Africa/Cairo", 2 * 3600, "Egypt"),
}
COUNTRY_CAPITAL_ALIASES = {
    "中国": "北京",
    "china": "beijing",
    "中华人民共和国": "北京",
    "美国": "washington dc",
    "usa": "washington dc",
    "us": "washington dc",
    "united states": "washington dc",
    "united states of america": "washington dc",
    "英国": "london",
    "uk": "london",
    "united kingdom": "london",
    "日本": "tokyo",
    "japan": "tokyo",
    "韩国": "seoul",
    "south korea": "seoul",
    "新加坡共和国": "singapore",
    "singapore": "singapore",
    "法国": "paris",
    "france": "paris",
    "德国": "berlin",
    "germany": "berlin",
    "意大利": "rome",
    "italy": "rome",
    "西班牙": "madrid",
    "spain": "madrid",
    "俄罗斯": "moscow",
    "russia": "moscow",
    "加拿大": "ottawa",
    "canada": "ottawa",
    "澳大利亚": "canberra",
    "australia": "canberra",
    "印度": "new delhi",
    "india": "new delhi",
    "泰国": "bangkok",
    "thailand": "bangkok",
    "阿联酋": "dubai",
    "uae": "dubai",
    "united arab emirates": "dubai",
    "墨西哥": "mexico city",
    "mexico": "mexico city",
    "巴西": "brasilia",
    "brazil": "brasilia",
    "埃及": "cairo",
    "egypt": "cairo",
}
CHINA_WEATHER_CITY_CODES = {
    "北京": "101010100",
    "beijing": "101010100",
    "上海": "101020100",
    "shanghai": "101020100",
    "天津": "101030100",
    "tianjin": "101030100",
    "重庆": "101040100",
    "chongqing": "101040100",
    "杭州": "101210101",
    "hangzhou": "101210101",
    "成都": "101270101",
    "chengdu": "101270101",
    "广州": "101280101",
    "guangzhou": "101280101",
    "深圳": "101280601",
    "shenzhen": "101280601",
    "南京": "101190101",
    "nanjing": "101190101",
    "苏州": "101190401",
    "suzhou": "101190401",
    "武汉": "101200101",
    "wuhan": "101200101",
    "西安": "101110101",
    "xi'an": "101110101",
    "xian": "101110101",
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


def fetch_ntp_time(host: str = "pool.ntp.org", timeout: float = 0.3) -> datetime:
    hosts = _unique_nonempty([*DEFAULT_NTP_HOSTS, host])
    for candidate in hosts:
        try:
            return _fetch_udp_ntp_time(candidate, timeout)
        except Exception:
            continue
    for url in HTTP_TIME_URLS:
        try:
            return _fetch_http_date_time(url, timeout=max(1.0, timeout))
        except Exception:
            continue
    return datetime.now(timezone.utc).replace(microsecond=0)


def _fetch_udp_ntp_time(host: str, timeout: float) -> datetime:
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


def _fetch_http_date_time(url: str, timeout: float) -> datetime:
    header = ""
    try:
        request = Request(
            url,
            headers={"User-Agent": "SmartClockHost/2.2"},
            method="HEAD",
        )
        with _open_url(request, timeout=timeout, prefer_proxy=False, allow_proxy=False) as response:
            header = response.headers.get("Date", "")
    except Exception:
        header = ""
    if not header:
        request = Request(url, headers={"User-Agent": "SmartClockHost/2.2"})
        with _open_url(request, timeout=timeout, prefer_proxy=False, allow_proxy=False) as response:
            header = response.headers.get("Date")
    if not header:
        raise RuntimeError("HTTP Date header missing")
    parsed = parsedate_to_datetime(header)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


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
        offset_seconds = infer_timezone_offset_seconds(
            timezone_name,
            default=(
                fallback_offset_seconds
                if fallback_offset_seconds is not None
                else DEFAULT_UTC_OFFSET_SECONDS
            ),
            utc_moment=moment,
        )
        fallback_tz = timezone(
            timedelta(seconds=offset_seconds),
            name=timezone_name,
        )
        return moment.astimezone(fallback_tz)


def infer_timezone_offset_seconds(
    timezone_name: str,
    default: int = DEFAULT_UTC_OFFSET_SECONDS,
    utc_moment: datetime | None = None,
) -> int:
    moment = utc_moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        offset = moment.astimezone(ZoneInfo(timezone_name)).utcoffset()
        if offset is not None:
            return int(offset.total_seconds())
    except (ZoneInfoNotFoundError, ModuleNotFoundError, ValueError):
        pass
    dst_offset = _dst_aware_offset_seconds(timezone_name, utc_moment)
    if dst_offset is not None:
        return dst_offset
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


def format_utc_offset(offset_seconds: int) -> str:
    sign = "+" if offset_seconds >= 0 else "-"
    absolute = abs(offset_seconds)
    hours, remainder = divmod(absolute, 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _dst_aware_offset_seconds(
    timezone_name: str,
    utc_moment: datetime | None,
) -> int | None:
    moment = utc_moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    year = moment.year
    if timezone_name == "America/New_York":
        start = _nth_weekday_utc(year, 3, 6, 2, 7)
        end = _nth_weekday_utc(year, 11, 6, 1, 6)
        return -4 * 3600 if start <= moment < end else -5 * 3600
    if timezone_name == "America/Chicago":
        start = _nth_weekday_utc(year, 3, 6, 2, 8)
        end = _nth_weekday_utc(year, 11, 6, 1, 7)
        return -5 * 3600 if start <= moment < end else -6 * 3600
    if timezone_name in {"America/Denver"}:
        start = _nth_weekday_utc(year, 3, 6, 2, 9)
        end = _nth_weekday_utc(year, 11, 6, 1, 8)
        return -6 * 3600 if start <= moment < end else -7 * 3600
    if timezone_name == "America/Los_Angeles":
        start = _nth_weekday_utc(year, 3, 6, 2, 10)
        end = _nth_weekday_utc(year, 11, 6, 1, 9)
        return -7 * 3600 if start <= moment < end else -8 * 3600
    if timezone_name in {"America/Toronto", "America/Detroit"}:
        start = _nth_weekday_utc(year, 3, 6, 2, 7)
        end = _nth_weekday_utc(year, 11, 6, 1, 6)
        return -4 * 3600 if start <= moment < end else -5 * 3600
    if timezone_name in {"America/Vancouver"}:
        start = _nth_weekday_utc(year, 3, 6, 2, 10)
        end = _nth_weekday_utc(year, 11, 6, 1, 9)
        return -7 * 3600 if start <= moment < end else -8 * 3600
    if timezone_name == "Australia/Sydney":
        start = _nth_weekday_utc(year, 10, 6, 1, 16)
        end = _nth_weekday_utc(year + 1, 4, 6, 1, 16) if moment.month >= 10 else _nth_weekday_utc(year, 4, 6, 1, 16)
        if moment.month >= 10:
            return 11 * 3600 if moment >= start else 10 * 3600
        return 11 * 3600 if moment < end else 10 * 3600
    if timezone_name == "Europe/London":
        start = _last_weekday_utc(year, 3, 6, 1)
        end = _last_weekday_utc(year, 10, 6, 1)
        return 1 * 3600 if start <= moment < end else 0
    if timezone_name in {"Europe/Paris", "Europe/Berlin"}:
        start = _last_weekday_utc(year, 3, 6, 1)
        end = _last_weekday_utc(year, 10, 6, 1)
        return 2 * 3600 if start <= moment < end else 1 * 3600
    if timezone_name == "Africa/Cairo":
        start = _last_weekday_utc(year, 4, 4, 22)
        end = _last_weekday_utc(year, 10, 3, 21)
        return 3 * 3600 if start <= moment < end else 2 * 3600
    return None


def _nth_weekday_utc(
    year: int,
    month: int,
    weekday: int,
    nth: int,
    hour_utc: int,
) -> datetime:
    day = datetime(year, month, 1, hour_utc, tzinfo=timezone.utc)
    days_until = (weekday - day.weekday()) % 7
    day = day + timedelta(days=days_until + (nth - 1) * 7)
    return day


def _last_weekday_utc(
    year: int,
    month: int,
    weekday: int,
    hour_utc: int,
) -> datetime:
    if month == 12:
        day = datetime(year + 1, 1, 1, hour_utc, tzinfo=timezone.utc)
    else:
        day = datetime(year, month + 1, 1, hour_utc, tzinfo=timezone.utc)
    day = day - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def geocode_city(city_name: str, timeout: float = 4.0) -> CityLookupResult:
    normalized = city_name.strip()
    lookup_key = normalized.lower()
    country_capital = COUNTRY_CAPITAL_ALIASES.get(lookup_key) or COUNTRY_CAPITAL_ALIASES.get(normalized)
    preset_query = country_capital or normalized
    preset = CITY_PRESETS.get(preset_query.lower()) or CITY_PRESETS.get(preset_query)
    if preset is not None:
        latitude, longitude, timezone_name, offset_seconds, country = preset
        offset_seconds = infer_timezone_offset_seconds(timezone_name, offset_seconds)
        return CityLookupResult(
            name=preset_query.title() if country_capital and preset_query.isascii() else preset_query,
            latitude=latitude,
            longitude=longitude,
            timezone=timezone_name,
            utc_offset_seconds=offset_seconds,
            country=normalized if country_capital else country,
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
    last_error: Exception | None = None
    try:
        return _fetch_china_weather(
            city_name,
            timezone_name,
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        last_error = exc
    try:
        return _fetch_open_meteo_weather(
            city_name,
            latitude,
            longitude,
            timezone_name,
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        last_error = exc if last_error is None else last_error
    try:
        return _fetch_wttr_weather(
            city_name,
            latitude,
            longitude,
            timezone_name,
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        if last_error is not None:
            raise RuntimeError(f"weather providers failed: {last_error}; {exc}") from exc
        raise


def _fetch_open_meteo_weather(
    city_name: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    timeout: float,
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


def _fetch_china_weather(
    city_name: str,
    timezone_name: str,
    timeout: float,
) -> WeatherSnapshot:
    city_code = _china_weather_city_code(city_name)
    if city_code is None:
        raise RuntimeError("china weather city code missing")
    payload = _fetch_json(
        f"http://t.weather.itboy.net/api/weather/city/{city_code}",
        timeout,
        prefer_proxy=False,
    )
    if int(payload.get("status", 0)) != 200:
        raise RuntimeError(str(payload.get("message", "china weather failed")))
    data = payload.get("data") or {}
    forecasts = data.get("forecast") or []
    today = forecasts[0] if forecasts else {}
    temp_c = _parse_temperature_value(data.get("wendu"))
    weather_type = str(today.get("type") or "")
    weather_code = _map_chinese_weather_type(weather_type)
    summary = weather_code_summary(weather_code)
    utc_offset_seconds = infer_timezone_offset_seconds(timezone_name)
    today_text = str(today.get("ymd") or datetime.now().date().isoformat())
    sunrise_at = _parse_hhmm_clock(today_text, today.get("sunrise"))
    sunset_at = _parse_hhmm_clock(today_text, today.get("sunset"))
    now_local = timezone_now(
        timezone_name,
        fallback_offset_seconds=utc_offset_seconds,
    ).replace(tzinfo=None)
    if sunrise_at is None:
        sunrise_at = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    if sunset_at is None:
        sunset_at = now_local.replace(hour=18, minute=0, second=0, microsecond=0)
    return WeatherSnapshot(
        city_name=city_name,
        temperature_c=temp_c,
        weather_code=weather_code,
        is_day=sunrise_at <= now_local < sunset_at,
        utc_offset_seconds=utc_offset_seconds,
        sunrise_at=sunrise_at,
        sunset_at=sunset_at,
        display_token=build_weather_token(summary, temp_c),
        led_mask=build_weather_led_mask(weather_code, temp_c),
        summary=summary,
    )


def _fetch_wttr_weather(
    city_name: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    timeout: float,
) -> WeatherSnapshot:
    coordinate_query = f"{latitude:.4f},{longitude:.4f}"
    city_query = quote(city_name.strip() or coordinate_query)
    urls = [
        f"https://wttr.in/{coordinate_query}?format=j1",
        f"https://wttr.in/{city_query}?format=j1",
    ]
    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for url in urls:
        try:
            payload = _fetch_json(url, timeout)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if payload is None:
        if last_error is not None:
            raise last_error
        raise RuntimeError("wttr payload missing")

    current_items = payload.get("current_condition") or []
    if not current_items:
        raise RuntimeError("wttr current_condition missing")
    current = current_items[0]
    temp_c = float(current.get("temp_C", 0.0))
    wttr_code = int(current.get("weatherCode", 0))
    weather_code = _map_wttr_weather_code(wttr_code)
    summary = weather_code_summary(weather_code)
    utc_offset_seconds = infer_timezone_offset_seconds(timezone_name)
    now_local = timezone_now(
        timezone_name,
        fallback_offset_seconds=utc_offset_seconds,
    ).replace(tzinfo=None)
    weather_items = payload.get("weather") or []
    sunrise_at = None
    sunset_at = None
    if weather_items:
        day_payload = weather_items[0]
        day_text = str(day_payload.get("date") or now_local.date().isoformat())
        astronomy = (day_payload.get("astronomy") or [{}])[0]
        sunrise_at = _parse_wttr_clock(day_text, astronomy.get("sunrise"))
        sunset_at = _parse_wttr_clock(day_text, astronomy.get("sunset"))
    if sunrise_at is None:
        sunrise_at = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    if sunset_at is None:
        sunset_at = now_local.replace(hour=18, minute=0, second=0, microsecond=0)

    return WeatherSnapshot(
        city_name=city_name,
        temperature_c=temp_c,
        weather_code=weather_code,
        is_day=sunrise_at <= now_local < sunset_at,
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


def _fetch_json(url: str, timeout: float, prefer_proxy: bool = True) -> dict[str, Any]:
    last_error: Exception | None = None
    request = Request(
        url,
        headers={
            "User-Agent": "SmartClockHost/2.2 (+https://github.com/Cyh29hao)",
            "Accept": "application/json",
        },
    )
    for attempt in range(2):
        try:
            with _open_url(request, timeout=timeout, prefer_proxy=prefer_proxy) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code < 500 or attempt == 1:
                raise
        except URLError as exc:
            last_error = exc
            if attempt == 1:
                raise
        time.sleep(0.4 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Unexpected fetch_json state")


def _open_url(request: Request, timeout: float, prefer_proxy: bool = True, allow_proxy: bool = True):
    last_error: Exception | None = None
    per_attempt_timeout = max(0.8, min(timeout, 2.0))
    for _label, opener in _network_openers(prefer_proxy=prefer_proxy, allow_proxy=allow_proxy):
        try:
            return opener.open(request, timeout=per_attempt_timeout)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return urlopen(request, timeout=timeout)


def _network_openers(prefer_proxy: bool = True, allow_proxy: bool = True):
    proxy_entries = []
    direct_entry = ("direct", build_opener(ProxyHandler({})))
    if not allow_proxy:
        return [direct_entry]
    for proxy_url in _candidate_proxy_urls():
        handler = ProxyHandler({"http": proxy_url, "https": proxy_url})
        proxy_entries.append((proxy_url, build_opener(handler)))
    entries = [*proxy_entries, direct_entry] if prefer_proxy else [direct_entry, *proxy_entries]
    if getproxies():
        entries.append(("system", build_opener()))
    seen: set[str] = set()
    unique_entries = []
    for label, opener in entries:
        if label in seen:
            continue
        seen.add(label)
        unique_entries.append((label, opener))
    return unique_entries


def _candidate_proxy_urls() -> list[str]:
    candidates: list[str] = []
    for value in getproxies().values():
        text = str(value).strip()
        if text and text.lower() != "direct://":
            candidates.append(_normalize_proxy_url(text))
    for port in LOCAL_HTTP_PROXY_PORTS:
        if _is_local_tcp_port_open(port):
            candidates.append(f"http://127.0.0.1:{port}")
    return _unique_nonempty(candidates)


def _normalize_proxy_url(value: str) -> str:
    if "://" in value:
        return value
    return f"http://{value}"


def _is_local_tcp_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.08):
            return True
    except OSError:
        return False


def _unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in result:
            result.append(item)
    return result


def _safe_parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_wttr_clock(day_text: str, clock_text: Any) -> datetime | None:
    if not clock_text:
        return None
    for pattern in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{day_text} {clock_text}", pattern)
        except ValueError:
            continue
    return None


def _parse_hhmm_clock(day_text: str, clock_text: Any) -> datetime | None:
    if not clock_text:
        return None
    for pattern in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(f"{day_text} {clock_text}", pattern)
        except ValueError:
            continue
    return None


def _parse_temperature_value(value: Any) -> float:
    text = str(value or "0").replace("℃", "").replace("°C", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _china_weather_city_code(city_name: str) -> str | None:
    normalized = city_name.strip().lower()
    simplified = normalized.removesuffix("市").replace(" ", "")
    return (
        CHINA_WEATHER_CITY_CODES.get(normalized)
        or CHINA_WEATHER_CITY_CODES.get(simplified)
        or CHINA_WEATHER_CITY_CODES.get(city_name.strip())
    )


def _map_chinese_weather_type(text: str) -> int:
    value = text.strip()
    if "雷" in value:
        return 95
    if "雪" in value or "冰" in value:
        return 71
    if "雨" in value:
        return 61
    if "雾" in value or "霾" in value:
        return 45
    if "晴" in value:
        return 0
    if "云" in value or "阴" in value:
        return 2
    return 2


def _map_wttr_weather_code(code: int) -> int:
    if code in {113}:
        return 0
    if code in {116, 119, 122}:
        return 2
    if code in {143, 248, 260}:
        return 45
    if code in {179, 182, 185, 227, 230, 317, 320, 323, 326, 329, 332, 335, 338, 350, 368, 371, 392, 395}:
        return 71
    if code in {176, 263, 266, 281, 284, 293, 296, 299, 302, 305, 308, 311, 314, 353, 356, 359, 362, 365}:
        return 61
    if code in {386, 389}:
        return 95
    return 2


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
