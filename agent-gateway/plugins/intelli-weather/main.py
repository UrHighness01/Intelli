"""intelli-weather plugin — current conditions + forecast via Open-Meteo.

Open-Meteo is free, open-source, and requires no API key.
Geocoding is done via the Open-Meteo geocoding API.
"""

from __future__ import annotations

import urllib.request
import json
from typing import Any, Dict, Optional, Tuple

_GEO_URL    = 'https://geocoding-api.open-meteo.com/v1/search'
_WEATHER_URL = 'https://api.open-meteo.com/v1/forecast'
_TIMEOUT    = 10

_WMO_CODES: Dict[int, str] = {
    0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
    45: 'Fog', 48: 'Depositing rime fog',
    51: 'Light drizzle', 53: 'Moderate drizzle', 55: 'Dense drizzle',
    61: 'Slight rain', 63: 'Moderate rain', 65: 'Heavy rain',
    71: 'Slight snow', 73: 'Moderate snow', 75: 'Heavy snow', 77: 'Snow grains',
    80: 'Slight showers', 81: 'Moderate showers', 82: 'Violent showers',
    85: 'Slight snow showers', 86: 'Heavy snow showers',
    95: 'Thunderstorm', 96: 'Thunderstorm with hail', 99: 'Thunderstorm with heavy hail',
}


def _fetch(url: str) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode())


def _geocode(location: str) -> Tuple[float, float, str]:
    """Return (lat, lon, display_name) for a location string."""
    # Check if it's already "lat,lon"
    parts = location.split(',')
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1]), location
        except ValueError:
            pass

    enc = urllib.request.quote(location)
    data = _fetch(f'{_GEO_URL}?name={enc}&count=1&language=en&format=json')
    results = data.get('results', [])
    if not results:
        raise ValueError(f'Location not found: {location}')
    r = results[0]
    name = f'{r.get("name", location)}, {r.get("country", "")}'.strip(', ')
    return float(r['latitude']), float(r['longitude']), name


def _temp_unit_param(units: str) -> str:
    return 'fahrenheit' if 'f' in units.lower() else 'celsius'


def _temp_sym(units: str) -> str:
    return '°F' if 'f' in units.lower() else '°C'


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

def weather_get(location: str, units: str = 'celsius') -> str:
    """Get current weather for *location*."""
    try:
        lat, lon, name = _geocode(location)
        unit_param  = _temp_unit_param(units)
        sym         = _temp_sym(units)
        url = (
            f'{_WEATHER_URL}?latitude={lat}&longitude={lon}'
            f'&current_weather=true'
            f'&hourly=relative_humidity_2m,apparent_temperature,precipitation_probability'
            f'&temperature_unit={unit_param}'
            f'&forecast_days=1'
        )
        data    = _fetch(url)
        cw      = data.get('current_weather', {})
        temp    = cw.get('temperature', 'N/A')
        wind    = cw.get('windspeed', 'N/A')
        wmo     = int(cw.get('weathercode', 0))
        cond    = _WMO_CODES.get(wmo, f'Code {wmo}')

        # Pull first hour values for humidity & apparent temp
        hourly  = data.get('hourly', {})
        hum     = (hourly.get('relative_humidity_2m') or [None])[0]
        feel    = (hourly.get('apparent_temperature')  or [None])[0]
        prec    = (hourly.get('precipitation_probability') or [None])[0]

        lines = [f'**Weather in {name}**']
        lines.append(f'- Condition: {cond}')
        lines.append(f'- Temperature: {temp}{sym}')
        if feel is not None:
            lines.append(f'- Feels like: {feel}{sym}')
        if hum is not None:
            lines.append(f'- Humidity: {hum}%')
        if prec is not None:
            lines.append(f'- Precipitation probability: {prec}%')
        lines.append(f'- Wind speed: {wind} km/h')
        return '\n'.join(lines)

    except Exception as exc:
        return f'[ERROR] weather_get: {exc}'


def weather_forecast(location: str, days: int = 3) -> str:
    """Get a multi-day forecast for *location*."""
    try:
        days = max(1, min(int(days), 7))
        lat, lon, name = _geocode(location)
        url = (
            f'{_WEATHER_URL}?latitude={lat}&longitude={lon}'
            f'&daily=weathercode,temperature_2m_max,temperature_2m_min,'
            f'precipitation_sum,windspeed_10m_max'
            f'&forecast_days={days}'
            f'&timezone=auto'
        )
        data   = _fetch(url)
        daily  = data.get('daily', {})
        dates  = daily.get('time', [])
        codes  = daily.get('weathercode', [])
        maxts  = daily.get('temperature_2m_max', [])
        mints  = daily.get('temperature_2m_min', [])
        precs  = daily.get('precipitation_sum', [])
        winds  = daily.get('windspeed_10m_max', [])

        lines = [f'**{days}-day forecast for {name}**\n']
        for i in range(min(days, len(dates))):
            cond  = _WMO_CODES.get(int(codes[i]) if i < len(codes) else 0, '')
            tmax  = f'{maxts[i]}°C' if i < len(maxts) else 'N/A'
            tmin  = f'{mints[i]}°C' if i < len(mints) else 'N/A'
            prec  = f'{precs[i]} mm' if i < len(precs) else 'N/A'
            wind  = f'{winds[i]} km/h' if i < len(winds) else 'N/A'
            lines.append(
                f'**{dates[i]}** — {cond}  |  '
                f'High {tmax} / Low {tmin}  |  '
                f'Rain {prec}  |  Wind {wind}'
            )
        return '\n'.join(lines)

    except Exception as exc:
        return f'[ERROR] weather_forecast: {exc}'
