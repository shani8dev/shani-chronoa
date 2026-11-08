"""Kundali (birth chart) and Panchang — pure Python, no external deps.

Computes Vedic birth charts (Rasi, Navamsha, etc.) and Panchang (Tithi,
Vara, Nakshatra, Yoga, Karana) entirely offline using Meeus astronomical
algorithms. No network, no API keys, no pip packages. Uses only the
standard library (math, datetime).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

# Nakshatra names (27)
NAKSHATRAS = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni",
    "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha",
    "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishta",
    "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada", "Revati",
]

TITHIS = [
    "Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
    "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
    "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi", "Purnima/Amavasya",
]

YOGAS = [
    "Vishkambha", "Priti", "Ayushman", "Saubhagya", "Shobhana",
    "Atiganda", "Sukarma", "Dhriti", "Shula", "Ganda",
    "Vriddhi", "Dhruva", "Vyaghata", "Harshana", "Vajra",
    "Siddhi", "Vyatipata", "Variyan", "Parigha", "Shiva",
    "Siddha", "Sadhya", "Shubha", "Shubha", "Brahma",
    "Indra", "Vaidhriti",
]

VARAS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

KARANAS = [
    "Bava", "Balava", "Kaulava", "Taitila", "Gara", "Vanija", "Vishti",
    "Shakuni", "Chatushpada", "Naga", "Kimstughna",
]


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "panchang").strip().lower()

    if action == "nakshatra":
        lon = float(arguments.get("longitude", 0))
        try:
            from shani_chronoa import astronomy
            info = astronomy.nakshatra_details(lon)
            return f"Nakshatra: {info['nakshatra']} (Pada {info['pada']}, Lord: {info['lord']})"
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "dignity":
        planet = arguments.get("planet", "Sun")
        sign = arguments.get("sign", "Aries")
        try:
            from shani_chronoa import astronomy
            info = astronomy.planet_info(planet, sign)
            return f"{planet} in {sign}: {info['dignity']}"
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "bazi":
        year = int(arguments.get("year", 2024))
        month = int(arguments.get("month", 1))
        day = int(arguments.get("day", 1))
        hour = int(arguments.get("hour", 12))
        try:
            from shani_chronoa import astronomy
            pillars = astronomy.bazi_pillars(year, month, day, hour)
            lines = [f"BaZi for {year}-{month:02d}-{day:02d} {hour:02d}:00:"]
            for name in ["year", "month", "day", "hour"]:
                p = pillars[name]
                lines.append(f"  {name.capitalize()}: {p['stem']}{p['branch']} ({p['element']})")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "aspects":
        date_str = arguments.get("date") or arguments.get("datetime")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not date_str:
            return "Provide a date."
        if "T" not in date_str:
            date_str = date_str + "T12:00:00Z"
        elif not date_str.endswith("Z"):
            date_str = date_str + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_chart(date_str, lat, lon)
            aspects = astronomy.compute_aspects(chart["planets"])
            if not aspects:
                return "No major aspects found."
            lines = ["Aspects:"]
            for a in aspects:
                lines.append(f"  {a['planet1']} {a['aspect']} {a['planet2']} (orb {a['orb']}°)")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "timelords":
        date_str = arguments.get("date") or arguments.get("datetime")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not date_str:
            return "Provide a birth datetime."
        if "T" not in date_str:
            date_str = date_str + "T12:00:00Z"
        elif not date_str.endswith("Z"):
            date_str = date_str + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(date_str, lat, lon)
            asc = chart["ascendant"]
            tl = astronomy.time_lords(date_str, asc, 0, 10)
            lines = ["Time Lords (first 10 years):", "Profections:"]
            for p in tl.get("profections", []):
                lines.append(f"  Age {p['age']}: {p['sign']} (lord: {p['lord']}, house {p['house']})")
            lines.append("Firdaria:")
            for f in tl.get("firdaria", []):
                lines.append(f"  {f['lord']}: {f['start']}-{f['end']} years")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "arabicparts":
        date_str = arguments.get("date") or arguments.get("datetime")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not date_str:
            return "Provide a datetime."
        if "T" not in date_str:
            date_str = date_str + "T12:00:00Z"
        elif not date_str.endswith("Z"):
            date_str = date_str + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(date_str, lat, lon)
            asc = chart["ascendant"]
            sun = next((p for p in chart["planets"] if p["name"] == "Sun"), {})
            moon = next((p for p in chart["planets"] if p["name"] == "Moon"), {})
            venus = next((p for p in chart["planets"] if p["name"] == "Venus"), {})
            parts = astronomy.arabic_parts(asc, sun.get("sidereal_longitude", 0), moon.get("sidereal_longitude", 0))
            lines = ["Arabic Parts (Lots):"]
            for name, val in parts.items():
                sign_idx = int(val / 30) % 12
                lines.append(f"  {name}: {astronomy.SIGNS[sign_idx]} {val % 30:.2f}°")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "temperament":
        date_str = arguments.get("date") or arguments.get("datetime")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not date_str:
            return "Provide a datetime."
        if "T" not in date_str:
            date_str = date_str + "T12:00:00Z"
        elif not date_str.endswith("Z"):
            date_str = date_str + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(date_str, lat, lon)
            temp = astronomy.temperament(chart["planets"])
            lines = [f"Temperament: {temp['primary_temperament']}"]
            for elem, pct in temp["percentages"].items():
                lines.append(f"  {elem}: {pct}%")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "lunarmansion":
        lon = float(arguments.get("longitude", 0))
        try:
            from shani_chronoa import astronomy
            return f"Lunar Mansion: {astronomy.lunar_mansion(lon)}"
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "charakarakas":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            karakas = astronomy.chara_karakas(chart["planets"])
            lines = ["Chara Karakas (Jaimini):"]
            for name, planet in karakas.items():
                lines.append(f"  {name}: {planet}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "shadbala":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            lines = ["Shadbala (Simplified):"]
            for p in chart["planets"][:7]:
                sb = astronomy.shadbala(p["name"], p["sign"], p.get("house", 1))
                lines.append(f"  {p['name']}: Uchcha={sb['uchcha_bala']} Kendra={sb['kendra_bala']} Ojh={sb['ojhayugma_bala']} Total={sb['total']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "combustion":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            sun = next((p for p in chart["planets"] if p["name"] == "Sun"), {})
            sun_lon = sun.get("sidereal_longitude", 0)
            lines = ["Combustion Check:"]
            for p in chart["planets"]:
                if p["name"] in ("Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn"):
                    comb = astronomy.is_combust(p["name"], p["sidereal_longitude"], sun_lon)
                    lines.append(f"  {p['name']}: {'Combust' if comb else 'Not combust'}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "horary":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        qh = int(arguments.get("quesited_house", 7))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            h = astronomy.horary_chart(dt, lat, lon, qh)
            lines = [f"Horary Chart for {dt}:"]
            lines.append(f"  Querent significator: {h['querent_significator']}")
            lines.append(f"  Quesited significator (House {qh}): {h['quesited_lord'] if 'quesited_lord' in h else h.get('quesited_significator', '')}")
            lines.append(f"  Same significator: {h['same_significator']}")
            lines.append(f"  Moon void of course: {h['moon_void_of_course']}")
            lines.append(f"  Planetary hour ruler: {h['planetary_hour_ruler']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "upagrahas":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            sun = next((p for p in chart["planets"] if p["name"] == "Sun"), {})
            sun_lon = sun.get("sidereal_longitude", 0)
            kala = astronomy.upagraha_kala(sun_lon)
            mrityu = astronomy.upagraha_mrityu(sun_lon)
            lines = ["Upagrahas:"]
            lines.append(f"  Kala: {astronomy.SIGNS[int(kala/30)%12]} {kala%30:.2f}°")
            lines.append(f"  Mrityu: {astronomy.SIGNS[int(mrityu/30)%12]} {mrityu%30:.2f}°")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "fullchart":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            # Return full chart as JSON
            import json
            return json.dumps(chart, indent=2, default=str)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "arudha":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            arudhas = astronomy.all_arudhas(chart["planets"], chart["houses"])
            lines = ["Arudha Padas:"]
            for name, sign in arudhas.items():
                lines.append(f"  {name}: {sign}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "drishti":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            asc_idx = int(chart["ascendant"] / 30) % 12
            drishtis = astronomy.find_vedic_aspects(chart["planets"], asc_idx)
            if not drishtis:
                return "No Vedic aspects found."
            lines = ["Vedic Drishti (Aspects):"]
            for d in drishtis[:20]:
                lines.append(f"  {d['from']} -> {d['to']}: {d['houses_away']} houses ({d['strength']}%)")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "vishesha":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            sun = next((p for p in chart["planets"] if p["name"] == "Sun"), {})
            sun_sid = sun.get("sidereal_longitude", 0)
            lines = ["Vishesha (Special) Lagnas:"]
            lines.append(f"  Hora Lagna: {astronomy.SIGNS[int(astronomy.hora_lagna_full(sun_sid, astronomy.julian_day(dt)) / 30) % 12]}")
            lines.append(f"  Ghati Lagna: {astronomy.SIGNS[int(astronomy.ghati_lagna_full(sun_sid) / 30) % 12]}")
            lines.append(f"  Bhava Lagna: {astronomy.SIGNS[int(astronomy.bhava_lagna_full(sun_sid) / 30) % 12]}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "muhurta":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            result = astronomy.muhurta_score(dt, lat, lon)
            lines = [f"Muhurta Score: {result['score']} ({result['verdict']})"]
            lines.append(f"  Vara: {result['vara']}")
            for f in result.get("factors", []):
                lines.append(f"  {f['code']}: {f['weight']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "vimshopaka":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            lines = ["Vimshopaka Bala (Shadvarga):"]
            for p in chart["planets"][:7]:
                vb = astronomy.vimshopaka_bala(p["name"], p["sign"], "shadvarga")
                lines.append(f"  {p['name']}: {vb}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "cheshta":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            lines = ["Cheshta (Motion States):"]
            for p in chart["planets"][:7]:
                state = astronomy.cheshta_state(1.0, 1.0)  # simplified
                lines.append(f"  {p['name']}: {state}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "grahayuddha":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            planets = chart["planets"]
            results = []
            for i in range(len(planets)):
                for j in range(i+1, len(planets)):
                    gy = astronomy.graha_yuddha(planets[i]["name"], planets[i]["sidereal_longitude"],
                                                planets[j]["name"], planets[j]["sidereal_longitude"])
                    if gy["in_war"]:
                        results.append(gy)
            if not results:
                return "No planetary wars found."
            lines = ["Graha Yuddha (Planetary Wars):"]
            for r in results:
                lines.append(f"  {r['planet1']} vs {r['planet2']}: sep {r['separation']}° (winner: {r['winner']})")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "upagrahas_full":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            sun = next((p for p in chart["planets"] if p["name"] == "Sun"), {})
            sun_sid = sun.get("sidereal_longitude", 0)
            upags = astronomy.all_upagrahas(sun_sid, True)
            lines = ["Upagrahas:"]
            for name, val in upags.items():
                sign_idx = int(val / 30) % 12
                lines.append(f"  {name}: {astronomy.SIGNS[sign_idx]} {val % 30:.2f}°")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "ashtakavarga":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            asc_sign = int(chart["ascendant"] / 30) % 12
            av = astronomy.ashtakavarga_full(chart["planets"], asc_sign)
            lines = ["Ashtakavarga (Sarva):"]
            for i, v in enumerate(av["sarva"]):
                lines.append(f"  {astronomy.SIGNS[i]}: {v}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "gochara":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            natal_moon = next((p for p in chart["planets"] if p["name"] == "Moon"), {})
            natal_moon_sign = int(natal_moon.get("sidereal_longitude", 0) / 30) % 12
            goch = astronomy.gochara(natal_moon_sign, chart["planets"])
            lines = ["Gochara (Transit from Moon):"]
            for planet, info in goch.items():
                fav = "Favorable" if info["favorable"] else "Unfavorable"
                lines.append(f"  {planet}: House {info['house_from_moon']} ({fav})")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "bhava":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            bh = astronomy.bhava_analysis(chart["planets"], chart["houses"])
            lines = ["Bhava (House) Analysis:"]
            for name, info in bh.items():
                tags = []
                if info["is_kendra"]: tags.append("Kendra")
                if info["is_trikona"]: tags.append("Trikona")
                if info["is_dusthana"]: tags.append("Dusthana")
                if info["is_upachaya"]: tags.append("Upachaya")
                planets = ", ".join(info["planets"]) if info["planets"] else "empty"
                lines.append(f"  {name}: {info['sign']} [{', '.join(tags)}] planets: {planets}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "kalamana":
        dt = arguments.get("datetime") or arguments.get("date")
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            jd = astronomy.julian_day(dt)
            km = astronomy.kala_mana(jd)
            kh = astronomy.kala_khanda(jd)
            lines = ["Kala Mana (Time):"]
            lines.append(f"  Julian Day: {km['julian_day']}")
            lines.append(f"  Day number: {km['day_number']}")
            lines.append(f"  Hour of day: {kh['hour_of_day']}")
            lines.append(f"  Ghati: {kh['ghati']}")
            lines.append(f"  Vighati: {kh['vighati']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "chinese":
        dt = arguments.get("datetime") or arguments.get("date")
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            cc = astronomy.chinese_calendar(dt)
            lines = ["Chinese Calendar:"]
            for k, v in cc.items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "grahana":
        dt = arguments.get("datetime") or arguments.get("date")
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            jd = astronomy.julian_day(dt)
            sun = astronomy._sun_longitude(jd)
            moon = astronomy._moon_longitude(jd)
            g = astronomy.khagola_grahana(sun, moon)
            lines = ["Grahana (Eclipse) Check:"]
            lines.append(f"  Solar eclipse: {g['solar_eclipse']}")
            lines.append(f"  Lunar eclipse: {g['lunar_eclipse']}")
            lines.append(f"  Angular separation: {g['angular_separation']}°")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "shadbala_full":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            jd = astronomy.julian_day(dt)
            lines = ["Shadbala (Complete):"]
            for p in chart["planets"][:7]:
                sb = astronomy.shadbala_complete(p["name"], p["sign"], p.get("house", 1), True,
                                                chart["planets"][1]["sidereal_longitude"] if len(chart["planets"]) > 1 else 0,
                                                chart["planets"][0]["sidereal_longitude"], jd)
                lines.append(f"  {p['name']}: Nais={sb['naisargika']} Kendra={sb['kendra']} Dig={sb['dig']} Cheshta={sb['cheshta']} Total={sb['total']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "karakas_full":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            karakas = astronomy.chara_karakas_full(chart["planets"])
            lines = ["Chara Karakas (Full, with Rahu reflection):"]
            for name, info in karakas.items():
                lines.append(f"  {name}: {info['planet']} ({info['degree']}°)")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "fixedstars":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            jd = astronomy.julian_day(dt)
            points = [p["sidereal_longitude"] for p in chart["planets"]]
            ayan = chart["ayanamsa"]
            stars = astronomy.fixed_stars_conjunct(jd, points, 2.0, True, ayan)
            if not stars:
                return "No fixed stars conjunct chart points."
            lines = ["Fixed Stars:"]
            for s in stars[:20]:
                lines.append(f"  {s['star']}: conjunct point at {s['point']}° (orb {s['orb']}°)")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "arabicparts_full":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            asc = chart["ascendant"]
            sun = next((p for p in chart["planets"] if p["name"] == "Sun"), {})
            moon = next((p for p in chart["planets"] if p["name"] == "Moon"), {})
            venus = next((p for p in chart["planets"] if p["name"] == "Venus"), {})
            mars = next((p for p in chart["planets"] if p["name"] == "Mars"), {})
            mercury = next((p for p in chart["planets"] if p["name"] == "Mercury"), {})
            jupiter = next((p for p in chart["planets"] if p["name"] == "Jupiter"), {})
            saturn = next((p for p in chart["planets"] if p["name"] == "Saturn"), {})
            parts = astronomy.arabic_parts_full(
                asc, sun.get("sidereal_longitude", 0), moon.get("sidereal_longitude", 0),
                venus.get("sidereal_longitude", 0), mars.get("sidereal_longitude", 0),
                mercury.get("sidereal_longitude", 0), jupiter.get("sidereal_longitude", 0),
                saturn.get("sidereal_longitude", 0))
            lines = ["Arabic Parts (Full):"]
            for name, val in parts.items():
                sign_idx = int(val / 30) % 12
                lines.append(f"  {name}: {astronomy.SIGNS[sign_idx]} {val % 30:.2f}°")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "fullpanchang":
        dt = arguments.get("date") or arguments.get("datetime")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a date."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            pc = astronomy.compute_full_panchang(dt, lat, lon)
            lines = [f"Full Panchang for {dt}:"]
            for k, v in pc.items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "yogas":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            yogas = astronomy.vishesha_yogas_full(chart["planets"])
            return "Vishesha Yogas:\n" + "\n".join(f"  {y}" for y in yogas)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "nodes":
        dt = arguments.get("datetime") or arguments.get("date")
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            jd = astronomy.julian_day(dt)
            mean = astronomy.node_mean(jd)
            true = astronomy.node_true(jd)
            osc = astronomy.node_osculating(jd)
            return f"Nodes:\n  Mean (Rahu): {mean:.4f}\n  True (Rahu): {true:.4f}\n  Osculating (Rahu): {osc:.4f}\n  Ketu: {(mean+180)%360:.4f}"
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "eclipse":
        dt = arguments.get("datetime") or arguments.get("date")
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            jd = astronomy.julian_day(dt)
            sun = astronomy._sun_longitude(jd)
            moon = astronomy._moon_longitude(jd)
            e = astronomy.eclipse_details(sun, moon, jd)
            lines = ["Eclipse Check:"]
            lines.append(f"  Solar eclipse: {e['solar_eclipse']}")
            lines.append(f"  Lunar eclipse: {e['lunar_eclipse']}")
            lines.append(f"  Angular separation: {e['angular_separation']}°")
            lines.append(f"  Magnitude: {e['magnitude']}")
            lines.append(f"  Type: {e['type']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "charadasha":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            asc_sign = int(chart["ascendant"] / 30) % 12
            positions = {p["name"]: int(p["sidereal_longitude"] / 30) % 12 for p in chart["planets"]}
            dashas = astronomy.chara_dasha(asc_sign, positions)
            lines = ["Chara Dasha (Jaimini):"]
            for d in dashas[:6]:
                lines.append(f"  {d['sign']}: {d['years']} years")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "yoginidasha":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            moon = next((p for p in chart["planets"] if p["name"] == "Moon"), {})
            dashas = astronomy.yogini_dasha(moon.get("sidereal_longitude", 0), astronomy.julian_day(dt))
            lines = ["Yogini Dasha:"]
            for d in dashas[:8]:
                lines.append(f"  {d['lord']}: {d['years']} years (from +{d['start_offset']})")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "ashtottaridasha":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            moon = next((p for p in chart["planets"] if p["name"] == "Moon"), {})
            dashas = astronomy.ashtottari_dasha(moon.get("sidereal_longitude", 0))
            lines = ["Ashtottari Dasha:"]
            for d in dashas:
                lines.append(f"  {d['lord']}: {d['years']} years (from +{d['start_offset']})")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "ziwei":
        year = int(arguments.get("year", 2024))
        month = int(arguments.get("month", 1))
        day = int(arguments.get("day", 1))
        hour = int(arguments.get("hour", 12))
        try:
            from shani_chronoa import astronomy
            zw = astronomy.zi_wei_basic(year, month, day, hour)
            lines = [f"Zi Wei Dou Shu for {year}-{month:02d}-{day:02d} {hour:02d}:00:"]
            lines.append(f"  Life Palace: {zw['life_palace']}")
            lines.append(f"  Bureau: {zw['bureau']}")
            lines.append("  Palaces:")
            for name, info in zw["palaces"].items():
                lines.append(f"    {name}: Palace {info['palace']}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "vishesha_yogas":
        dt = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not dt:
            return "Provide a datetime."
        if "T" not in dt:
            dt = dt + "T12:00:00Z"
        elif not dt.endswith("Z"):
            dt = dt + "Z"
        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(dt, lat, lon)
            yogas = astronomy.vishesha_yogas_comprehensive(chart["planets"])
            return "Vishesha Yogas:\n" + "\n".join(f"  {y}" for y in yogas)
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "meanmotion":
        planet = arguments.get("planet", "Sun")
        try:
            from shani_chronoa import astronomy
            mm = astronomy.mean_motion_full(planet)
            return f"{mm['planet']}: {mm['degrees_per_day']}°/day ({mm['degrees_per_year']}°/year)"
        except Exception as exc:
            return f"Failed: {exc}"

    if action == "panchang":
        # Panchang for a given date and location
        date_str = arguments.get("date") or arguments.get("datetime")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not date_str:
            return "Provide a date (e.g. '2024-01-01' or '2024-01-01T12:00:00Z')."

        # Normalize date
        if "T" not in date_str:
            date_str = date_str + "T12:00:00Z"
        elif not date_str.endswith("Z"):
            date_str = date_str + "Z"

        try:
            from shani_chronoa import astronomy
            panchang = astronomy.compute_panchang(date_str, lat, lon)
            lines = [
                f"Panchang for {date_str} ({lat}, {lon}):",
                f"  Tithi: {panchang['tithi']}",
                f"  Vara: {panchang['vara']}",
                f"  Nakshatra: {panchang['nakshatra']}",
                f"  Yoga: {panchang['yoga']}",
                f"  Karana: {panchang['karana']}",
                f"  Sun: {panchang['sun_longitude']}°  Moon: {panchang['moon_longitude']}°",
                f"  Ayanamsa (Lahiri): {panchang['ayanamsa']}°",
            ]
            return "\n".join(lines)
        except Exception as exc:
            return f"Panchang calculation failed: {exc}"

    elif action == "kundali" or action == "chart":
        # Birth chart
        date_str = arguments.get("datetime") or arguments.get("date")
        lat = float(arguments.get("latitude", 28.6139))
        lon = float(arguments.get("longitude", 77.2090))
        if not date_str:
            return "Provide a birth datetime (e.g. '1990-05-15T14:30:00Z')."

        if "T" not in date_str:
            date_str = date_str + "T12:00:00Z"
        elif not date_str.endswith("Z"):
            date_str = date_str + "Z"

        try:
            from shani_chronoa import astronomy
            chart = astronomy.compute_full_chart_v2(date_str, lat, lon)

            lines = [f"Kundali (Sidereal, Lahiri) for {date_str} ({lat}, {lon}):", ""]
            lines.append(f"Ascendant: {chart['ascendant_sign']} {int(chart['ascendant'] % 30)}°")
            lines.append(f"Ayanamsa (Lahiri): {chart['ayanamsa']}°")
            lines.append("")
            lines.append("Planets:")
            for p in chart.get("planets", []):
                retro = " (R)" if p.get("retrograde") else ""
                dig = p.get("dignity", "")
                lines.append(f"  {p['name']}: {p['sign']} {p['degree']}°{p['minute']:02d}'{retro} ({dig})")

            lines.append("")
            lines.append("Houses (Whole Sign):")
            for h in chart.get("houses", []):
                lines.append(f"  House {h['house_number']}: {h['sign']} {h['degree']}°{h['minute']:02d}'")

            # Navamsha (D9)
            lines.append("")
            lines.append("Navamsha (D9):")
            for p in chart.get("planets", []):
                v = astronomy.varga_longitude(p["sidereal_longitude"], "D9")
                sign_idx = int(v / 30) % 12
                lines.append(f"  {p['name']}: {astronomy.SIGNS[sign_idx]} {int(v % 30)}°")

            # Aspects
            aspects = chart.get("aspects", [])
            if aspects:
                lines.append("")
                lines.append("Aspects:")
                for a in aspects[:10]:
                    lines.append(f"  {a['planet1']} {a['aspect']} {a['planet2']} (orb {a['orb']}°)")

            # Vimshottari Dasha
            moon = next((p for p in chart["planets"] if p["name"] == "Moon"), None)
            if moon and chart.get("dasha"):
                dashas = chart["dasha"].get("mahadashas", [])
                lines.append("")
                lines.append("Vimshottari Dasha:")
                for d in dashas[:5]:
                    lines.append(f"  {d['lord']}: {d['years']} years (from +{d['start_offset']})")

            # Lunar status
            ls = chart.get("lunar_status", {})
            if ls:
                lines.append("")
                lines.append(f"Moon: {ls.get('nakshatra')} | Illumination: {ls.get('illumination')}% | Age: {ls.get('age_days')} days")

            # Planetary hours
            ph = chart.get("planetary_hours", {})
            if ph:
                lines.append(f"Planetary Hour: {ph.get('planetary_hour_ruler')} ({ph.get('day')})")

            return "\n".join(lines)
        except Exception as exc:
            return f"Kundali calculation failed: {exc}"

    return "Unknown action. Use 'panchang' or 'kundali'."


SCHEMA = {
    "type": "function",
    "function": {
        "name": "kundali",
        "description": (
            "Compute Vedic Kundali (birth chart), Panchang (daily almanac), "
            "transits, aspects, dignities, BaZi (Chinese Four Pillars), and Nakshatra details. "
            "Offline, no API keys, no external dependencies. "
            "panchang: Tithi, Vara, Nakshatra, Yoga, Karana. "
            "kundali: sidereal birth chart with planets, houses, Navamsha, Dasha, aspects, dignities. "
            "transit: transit positions and cross-aspects. aspects: planetary aspects. "
            "dignity: planet dignity in a sign. bazi: Chinese Four Pillars. "
            "nakshatra: nakshatra details. timelords: profections and firdaria. "
            "arabicparts: Arabic parts (lots). temperament: elemental temperament. "
            "lunarmansion: lunar mansion. charakarakas: Jaimini karakas. "
            "shadbala: planetary strength. combustion: combust planets. "
            "horary: horary chart. upagrahas: all upagrahas. "
            "arudha: Arudha padas. drishti: Vedic aspects. vishesha: special lagnas. "
            "muhurta: electional scoring. vimshopaka: Vimshopaka bala. "
            "cheshta: motion states. grahayuddha: planetary wars. "
            "upagrahas_full: all upagrahas. fullchart: complete chart JSON. "
            "ashtakavarga: Bhinna + Sarva ashtakavarga. gochara: transit analysis. "
            "bhava: house analysis. kalamana: time measurement. chinese: Chinese calendar. "
            "grahana: eclipse check. shadbala_full: complete shadbala. "
            "karakas_full: chara karakas with Rahu reflection. "
            "yogas: Vishesha yogas. nodes: lunar nodes. eclipse: eclipse details. "
            "charadasha: Chara dasha. yoginidasha: Yogini dasha. "
            "ashtottaridasha: Ashtottari dasha."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["panchang", "kundali", "transit", "aspects", "dignity", "bazi", "nakshatra", "timelords", "arabicparts", "temperament", "lunarmansion", "charakarakas", "shadbala", "combustion", "horary", "upagrahas", "fullchart", "arudha", "drishti", "vishesha", "muhurta", "vimshopaka", "cheshta", "grahayuddha", "upagrahas_full", "ashtakavarga", "gochara", "bhava", "kalamana", "chinese", "grahana", "shadbala_full", "karakas_full", "fixedstars", "arabicparts_full", "fullpanchang", "yogas", "nodes", "eclipse", "charadasha", "yoginidasha", "ashtottaridasha", "ziwei", "vishesha_yogas", "meanmotion"],
                           "description": "panchang, kundali, transit, aspects, dignity, bazi (Chinese), or nakshatra details."},
                "date": {"type": "string", "description": "Date (YYYY-MM-DD or ISO datetime)."},
                "datetime": {"type": "string", "description": "Birth datetime (ISO, e.g. '1990-05-15T14:30:00Z')."},
                "latitude": {"type": "number", "description": "Latitude (default: Delhi 28.6139)."},
                "longitude": {"type": "number", "description": "Longitude (default: Delhi 77.2090)."},
                "planet": {"type": "string", "description": "Planet name for dignity check."},
                "sign": {"type": "string", "description": "Zodiac sign for dignity check."},
                "year": {"type": "integer", "description": "Year for BaZi."},
                "month": {"type": "integer", "description": "Month for BaZi."},
                "day": {"type": "integer", "description": "Day for BaZi."},
                "hour": {"type": "integer", "description": "Hour for BaZi."},
            },
            "required": ["action"],
        },
    },
}

SKILLS = [Skill(name="kundali", schema=SCHEMA, run=_run)]
