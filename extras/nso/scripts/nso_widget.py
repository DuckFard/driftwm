#!/usr/bin/env python3
"""Native driftwm widgets for the Needy Streamer Overload Rainmeter port.

This intentionally does not start HTTP, WebKit, Firefox, or a browser UI.
Each skin is a small GTK/Cairo Wayland toplevel that driftwm turns into a
canvas widget through window rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


APP_NAME = "driftwm-nso"
ROOT = Path(os.environ.get("NSO_DRIFTWM_ROOT", Path(__file__).resolve().parents[1])).resolve()
UPSTREAM = ROOT / "assets" / "Needy-Streamer-Overload"
IMAGES = UPSTREAM / "@Resources" / "Images"
FONTS = UPSTREAM / "@Resources" / "Fonts"
DEFAULT_CONFIG = ROOT / "config" / "default.toml"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "driftwm"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_NAME
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "nso.toml"
STATE_FILE = STATE_DIR / "state.json"
NOTES_FILE = DATA_DIR / "quick-notes.txt"
WEATHER_ERROR_REFRESH_SECONDS = 120


def _install_fontconfig() -> None:
    """Point fontconfig at the bundled NSO bitmap fonts."""
    if os.environ.get("FONTCONFIG_FILE"):
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fontconfig = STATE_DIR / "fonts.conf"
    fontconfig.write_text(
        f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>{FONTS}</dir>
  <cachedir>{STATE_DIR / "font-cache"}</cachedir>
</fontconfig>
""",
        encoding="utf-8",
    )
    os.environ["FONTCONFIG_FILE"] = str(fontconfig)


_install_fontconfig()

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

try:  # noqa: E402
    import cairo  # type: ignore
except ImportError:  # pragma: no cover - optional pixel filter dependency
    cairo = None


PURPLE = (77 / 255, 35 / 255, 207 / 255)
PURPLE_DARK = (77 / 255, 33 / 255, 203 / 255)
PINK = (255 / 255, 248 / 255, 255 / 255)
BLUE = (110 / 255, 181 / 255, 223 / 255)
WARN = (231 / 255, 83 / 255, 83 / 255)
WHITE = (1, 1, 1)
BLACK = (0, 0, 0)
PLACEHOLDER_VALUES = {"paste here!", "paste here", "none", "null", "changeme", "your-api-key", "your-city-id"}
MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

CLOSE_POS = {
    "welcome": (764, 11),
    "task-manager": (369, 11),
    "jine": (284, 11),
    "social-media": (369, 11),
    "media-player": (369, 11),
    "calendar": (369, 11),
    "desktop-icons": (369, 11),
    "quick-notes": (369, 11),
    "medications": (144, 11),
}


@dataclass(frozen=True)
class WidgetSpec:
    key: str
    app_id: str
    title: str
    width: int
    height: int


SPECS: dict[str, WidgetSpec] = {
    "welcome": WidgetSpec("welcome", "dev.driftwm.nso.welcome", "NSO Welcome", 800, 670),
    "task-manager": WidgetSpec("task-manager", "dev.driftwm.nso.task_manager", "NSO Task Manager", 405, 361),
    "ame": WidgetSpec("ame", "dev.driftwm.nso.ame", "NSO Ame", 368, 291),
    "jine": WidgetSpec("jine", "dev.driftwm.nso.jine", "NSO JINE", 320, 371),
    "social-media": WidgetSpec("social-media", "dev.driftwm.nso.social_media", "NSO Social Media", 405, 504),
    "media-player": WidgetSpec("media-player", "dev.driftwm.nso.media_player", "NSO Media Player", 405, 172),
    "calendar": WidgetSpec("calendar", "dev.driftwm.nso.calendar", "NSO Calendar", 405, 172),
    "desktop-icons": WidgetSpec("desktop-icons", "dev.driftwm.nso.desktop_icons", "NSO Desktop Icons", 405, 361),
    "quick-notes": WidgetSpec("quick-notes", "dev.driftwm.nso.quick_notes", "NSO Quick Notes", 405, 361),
    "medications": WidgetSpec("medications", "dev.driftwm.nso.medications", "NSO Medications", 180, 280),
}


ALIASES = {
    "task": "task-manager",
    "social": "social-media",
    "media": "media-player",
    "icons": "desktop-icons",
    "notes": "quick-notes",
    "meds": "medications",
}


LAUNCH_ORDER = [
    "welcome",
    "task-manager",
    "ame",
    "jine",
    "social-media",
    "media-player",
    "calendar",
    "desktop-icons",
    "quick-notes",
    "medications",
]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_command(args: list[str], timeout: float = 0.8) -> str:
    try:
        completed = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def parse_media_seconds(value: str, *, microseconds: bool = False) -> int:
    text = value.strip()
    if not text:
        return 0
    if re.fullmatch(r"\d+(?::\d{1,2}){1,2}(?:\.\d+)?", text):
        parts = text.split(":")
        seconds = float(parts[-1])
        minutes = int(parts[-2])
        hours = int(parts[-3]) if len(parts) == 3 else 0
        return max(0, int(hours * 3600 + minutes * 60 + seconds))
    try:
        number = float(text)
    except ValueError:
        return 0
    if microseconds and number > 10_000:
        number /= 1_000_000
    return max(0, int(number))


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if value < 1024 or unit == "TiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def expand_config_value(value: str) -> str:
    return (
        value.replace("$HOME", str(Path.home()))
        .replace("$CONFIG_FILE", str(CONFIG_FILE))
        .replace("$NOTES_FILE", str(NOTES_FILE))
    )


def is_placeholder(value: str) -> bool:
    text = value.strip().lower()
    return not text or text in PLACEHOLDER_VALUES


class NsoState:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.config = deep_merge(read_toml(DEFAULT_CONFIG), read_toml(CONFIG_FILE))
        self.state = self._initial_state()
        self.state = deep_merge(self.state, read_json(STATE_FILE, {}))
        self.dialogue = self._load_dialogue()
        self.tweets = self._load_tweets()
        self.cpu_sample = self._parse_cpu_line()
        self.media_art_cache: dict[str, GdkPixbuf.Pixbuf] = {}
        if not NOTES_FILE.exists():
            NOTES_FILE.write_text("stream idea:\ncheck system updates\nwater break\n", encoding="utf-8")

    def _initial_state(self) -> dict[str, Any]:
        ame = self.config.get("ame", {})
        return {
            "ame": {
                "love": int(ame.get("default_love", 20)),
                "darkness": int(ame.get("default_darkness", 6)),
                "activity": "idle",
                "headpats": 0,
                "headpat_dialogue": 0,
                "last_headpat_at": 0,
            },
            "jine": {"index": 0, "last_sent": int(time.time()) - 4, "last_sticker": "", "last_reply": ""},
            "social": {"index": 0, "next_mode": "sequential", "engagement": {}},
            "notes": {"title": self.config.get("quick_notes", {}).get("default_title", "Quick Notes")},
            "welcome": {"warning_accepted": False},
            "weather_cache": {},
        }

    def save(self) -> None:
        write_json(STATE_FILE, self.state)

    @staticmethod
    def _parse_cpu_line() -> tuple[int, int]:
        try:
            parts = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
            numbers = [int(part) for part in parts]
            idle = numbers[3] + numbers[4]
            return idle, sum(numbers)
        except (OSError, IndexError, ValueError):
            return 0, 0

    def system_state(self) -> dict[str, Any]:
        previous_idle, previous_total = self.cpu_sample
        idle, total = self._parse_cpu_line()
        self.cpu_sample = (idle, total)
        delta = max(1, total - previous_total)
        cpu = max(0, min(100, int(100 * (delta - max(0, idle - previous_idle)) / delta)))

        mem: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, value = line.split(":", 1)
                mem[key] = int(value.strip().split()[0])
        except (OSError, ValueError):
            mem = {"MemTotal": 1, "MemAvailable": 0}
        total_mem = max(1, mem.get("MemTotal", 1))
        used_mem = total_mem - mem.get("MemAvailable", 0)
        memory = int(used_mem / total_mem * 100)

        disk_path = self.config.get("task_manager", {}).get("disk_path", "/")
        usage = shutil.disk_usage(disk_path)
        uptime_seconds = int(float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0]))
        disk = int(usage.used / max(1, usage.total) * 100)
        return {
            "cpu": cpu,
            "memory": memory,
            "disk": disk,
            "memory_label": f"{used_mem / 1048576:.1f}/{total_mem / 1048576:.1f} GiB",
            "disk_label": f"{human_bytes(usage.used)}/{human_bytes(usage.total)}",
            "uptime_seconds": uptime_seconds,
            "uptime": f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60:02d}m",
        }

    def ame_is_stream_time(self) -> bool:
        cfg = self.config.get("ame", {})
        hour = datetime.now().hour
        start = int(cfg.get("stream_start_hour", 22))
        end = int(cfg.get("stream_end_hour", 5))
        if start == end:
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def ame_state(self, system: dict[str, Any], headpat_hover: bool = False) -> dict[str, Any]:
        cfg = self.config.get("ame", {})
        ame = self.state.setdefault("ame", {})
        stress = max(0, min(100, int((system["cpu"] + system["memory"]) / 2)))
        love = max(0, min(100, int(ame.get("love", cfg.get("default_love", 20)))))
        darkness = max(0, min(100, int(ame.get("darkness", cfg.get("default_darkness", 6)))))
        activity = str(ame.get("activity", "idle"))
        variation = int(time.time() // 15) % 2
        streaming = activity == "stream" or (activity == "idle" and self.ame_is_stream_time())
        if streaming:
            sprite = self.ame_stream_sprite()
        elif activity in {"game", "movie"}:
            sprite = self.ame_activity_sprite(activity, love, darkness)
        elif headpat_hover:
            sprite = self.ame_sprite_for(0, love, darkness, 1)
        else:
            sprite = self.ame_sprite_for(stress, love, darkness, variation)
        return {
            "stress": stress,
            "love": love,
            "darkness": darkness,
            "streaming": streaming,
            "activity": activity,
            "sprite": sprite,
            "background": self.ame_background(system["uptime_seconds"]),
            "headpats": int(ame.get("headpats", 0)),
            "headpat_dialogue": max(0, min(4, int(ame.get("headpat_dialogue", 0)))),
            "last_headpat_at": float(ame.get("last_headpat_at", 0) or 0),
        }

    @staticmethod
    def ame_background(uptime_seconds: int) -> str:
        hours = uptime_seconds / 3600
        for threshold in [7283, 3654, 1000, 500, 250, 100, 10, 2, 1]:
            if hours >= threshold:
                return f"bg/{threshold}.png"
        return "bg/0.png"

    @staticmethod
    def ame_stat_band(value: int) -> str:
        if value >= 80:
            return "2"
        if value >= 60:
            return "1"
        return "0"

    @classmethod
    def ame_sprite_for(cls, stress: int, love: int, darkness: int, variation: int = 0) -> str:
        stress_dir = "1" if stress >= 80 else "0"
        love_dir = cls.ame_stat_band(love)
        dark_dir = cls.ame_stat_band(darkness)
        variation_dir = "1" if variation else "0"
        candidates = [
            f"sprites/{stress_dir}/{love_dir}/{dark_dir}/{variation_dir}/0.png",
            f"sprites/{stress_dir}/{love_dir}/{dark_dir}/0/0.png",
            f"sprites/{stress_dir}/{love_dir}/0/0/0.png",
            f"sprites/{stress_dir}/0/0/0/0.png",
            "sprites/0.png",
        ]
        for rel in candidates:
            if (IMAGES / "Ame" / rel).exists():
                return rel
        return "sprites/0.png"

    @classmethod
    def ame_activity_sprite(cls, activity: str, love: int, darkness: int) -> str:
        love_dir = cls.ame_stat_band(love)
        dark_dir = cls.ame_stat_band(darkness)
        activity_dir = "1" if activity == "movie" else "0"
        candidates = [
            f"sprites/-1/{love_dir}/{dark_dir}/{activity_dir}/0.png",
            f"sprites/-1/{love_dir}/{dark_dir}/0/0.png",
            "sprites/-1/-1/-1/0/0.png",
            "sprites/0.png",
        ]
        for rel in candidates:
            if (IMAGES / "Ame" / rel).exists():
                return rel
        return "sprites/0.png"

    @staticmethod
    def ame_stream_sprite() -> str:
        second = int(time.time() // 8)
        try:
            streams = [
                path
                for path in (IMAGES / "Ame" / "sprites" / "stream").iterdir()
                if path.is_dir() and path.name.isdigit()
            ]
        except OSError:
            streams = []
        if streams:
            stream = sorted(streams, key=lambda path: int(path.name))[second % len(streams)]
            try:
                sections = [path for path in stream.iterdir() if path.is_dir() and path.name.isdigit()]
            except OSError:
                sections = []
            if sections:
                section = sorted(sections, key=lambda path: int(path.name))[(second // len(streams)) % len(sections)]
                frame = section / "0.png"
                if frame.exists():
                    return str(frame.relative_to(IMAGES / "Ame"))
        return "sprites/0.png"

    def _load_dialogue(self) -> dict[str, list[str]]:
        base = UPSTREAM / "JINE" / "Raw JINE Text Files"
        out: dict[str, list[str]] = {}
        for path in sorted(base.glob("*.txt")):
            key = "default" if path.stem.upper() == "JINE" else path.stem.lower()
            try:
                lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
            except OSError:
                lines = []
            out[key] = [line for line in lines if line]
        return out

    def _load_tweets(self) -> list[dict[str, Any]]:
        path = UPSTREAM / "Social Media" / "scripts" / "tweets.lua"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        pattern = re.compile(
            r"a\[(\d+)\]\s*=\s*\{\s*\"((?:[^\"\\]|\\.)*)\"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*\"([^\"]*)\"",
            re.S,
        )
        tweets = []
        for match in pattern.finditer(text):
            tweet_id = int(match.group(1))
            image = f"{tweet_id}.png"
            if not (IMAGES / "Social Media" / "Tweets" / image).exists():
                image = ""
            try:
                body = json.loads(f'"{match.group(2)}"')
            except json.JSONDecodeError:
                body = match.group(2).replace(r"\n", "\n").replace(r"\"", '"')
            tweets.append(
                {
                    "id": tweet_id,
                    "text": body,
                    "retweets": int(match.group(3)),
                    "likes": int(match.group(4)),
                    "user": match.group(5),
                    "image": image,
                }
            )
        return sorted(tweets, key=lambda t: t["id"])

    def jine_reply(self, sticker: str) -> str:
        replies = self.dialogue.get(sticker, []) or self.dialogue.get("default", [])
        reply = replies[random.randrange(len(replies))] if replies else ""
        state = self.state.setdefault("jine", {})
        state["index"] = int(state.get("index", 0)) + 1
        state["last_sent"] = int(time.time())
        state["last_sticker"] = sticker
        state["last_reply"] = reply
        self.save()
        return reply

    def current_tweet(self) -> dict[str, Any]:
        if not self.tweets:
            return {"id": 0, "text": "No posts loaded.", "retweets": 0, "likes": 0, "user": "ame", "image": ""}
        social = self.state.setdefault("social", {})
        index = int(social.get("index", 0)) % len(self.tweets)
        tweet = dict(self.tweets[index])
        uptime_h = self.system_state()["uptime_seconds"] // 3600
        cfg = self.config.get("social_media", {})
        tweet["retweets"] += int(uptime_h // max(1, int(cfg.get("screen_time_retweet_scale", 8))))
        tweet["likes"] += int(uptime_h // max(1, int(cfg.get("screen_time_like_scale", 11))))
        engagement = social.setdefault("engagement", {})
        boosts = engagement.get(str(tweet.get("id", index)), {})
        tweet["retweets"] += int(boosts.get("retweets", 0))
        tweet["likes"] += int(boosts.get("likes", 0))
        return tweet

    def next_tweet(self) -> None:
        state = self.state.setdefault("social", {})
        if not self.tweets:
            return
        current = int(state.get("index", 0))
        if self.social_next_mode() == "random" and len(self.tweets) > 1:
            choices = [index for index in range(len(self.tweets)) if index != current % len(self.tweets)]
            state["index"] = random.choice(choices)
        else:
            state["index"] = current + 1
        self.save()

    def social_next_mode(self) -> str:
        mode = str(self.state.setdefault("social", {}).get("next_mode", "sequential"))
        return "random" if mode == "random" else "sequential"

    def set_social_next_mode(self, mode: str) -> None:
        self.state.setdefault("social", {})["next_mode"] = "random" if mode == "random" else "sequential"
        self.save()

    def social_engage(self, kind: str) -> None:
        tweet = self.current_tweet()
        tweet_id = str(tweet.get("id", 0))
        key = "retweets" if kind == "retweet" else "likes"
        social = self.state.setdefault("social", {})
        engagement = social.setdefault("engagement", {})
        boosts = engagement.setdefault(tweet_id, {})
        boosts[key] = int(boosts.get(key, 0)) + 1
        self.save()

    def media_state(self) -> dict[str, Any]:
        player = self.config.get("media_player", {}).get("preferred_player", "")
        base = ["playerctl"]
        if player:
            base += ["--player", player]
        status = run_command(base + ["status"])
        if not status:
            return {"available": False, "status": "Idle", "title": "No active media", "artist": "", "position": 0, "duration": 0, "art": "", "special": ""}
        title = run_command(base + ["metadata", "title"]) or "Unknown title"
        artist = run_command(base + ["metadata", "artist"]) or "Unknown artist"
        duration_raw = run_command(base + ["metadata", "mpris:length"])
        duration_label_raw = run_command(base + ["metadata", "--format", "{{duration(mpris:length)}}"])
        position_raw = run_command(base + ["position"])
        art = run_command(base + ["metadata", "mpris:artUrl"])
        source = run_command(base + ["metadata", "xesam:url"])
        if not art:
            art = self.derived_art_url(source)
        duration = parse_media_seconds(duration_raw, microseconds=True) or parse_media_seconds(duration_label_raw)
        position = parse_media_seconds(position_raw)
        if duration and position > duration:
            position = duration
        special = ""
        lower = f"{title} {artist}".lower()
        for needle, image in self.config.get("media_player", {}).get("special_title_images", {}).items():
            if needle in lower:
                special = image
                break
        return {
            "available": True,
            "status": status,
            "title": title,
            "artist": artist,
            "position": position,
            "duration": duration,
            "art": art,
            "special": special,
        }

    @staticmethod
    def derived_art_url(source_url: str) -> str:
        if not source_url:
            return ""
        parsed = urllib.parse.urlparse(source_url)
        host = parsed.netloc.lower()
        video_id = ""
        if host in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.strip("/").split("/")[0]
        elif host.endswith("youtube.com"):
            if parsed.path == "/watch":
                video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
            elif parsed.path.startswith(("/shorts/", "/embed/")):
                video_id = parsed.path.strip("/").split("/", 1)[1]
        if not video_id or not re.fullmatch(r"[-_A-Za-z0-9]{6,}", video_id):
            return ""
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    def media_art_path(self, art_url: str) -> Path | None:
        if not art_url:
            return None
        parsed = urllib.parse.urlparse(art_url)
        if parsed.scheme == "file":
            path = Path(urllib.parse.unquote(parsed.path))
            return path if path.is_file() else None
        if parsed.scheme in {"http", "https"}:
            digest = hashlib.sha256(art_url.encode("utf-8")).hexdigest()[:16]
            cache = STATE_DIR / "media-art" / f"{digest}.jpg"
            if cache.exists():
                return cache
            cache.parent.mkdir(parents=True, exist_ok=True)
            try:
                with urllib.request.urlopen(art_url, timeout=4) as response:
                    cache.write_bytes(response.read())
                return cache
            except Exception:
                return None
        path = Path(art_url)
        return path if path.is_file() else None

    @staticmethod
    def _weather_config_value(cfg: dict[str, Any], *names: str, env: str | None = None) -> str:
        for name in names:
            value = cfg.get(name)
            if value is not None:
                text = str(value).strip()
                if not is_placeholder(text):
                    return text
        if env:
            text = os.environ.get(env, "").strip()
            if not is_placeholder(text):
                return text
        return ""

    def weather_state(self) -> dict[str, Any]:
        calendar_cfg = self.config.get("calendar", {})
        cfg = deep_merge(
            {key: value for key, value in calendar_cfg.items() if key != "weather"},
            calendar_cfg.get("weather", {}),
        )
        api_key = self._weather_config_value(
            cfg,
            "api_key",
            "ApiKey",
            env="NSO_OPENWEATHER_API_KEY",
        ) or self._weather_config_value(cfg, env="OPENWEATHERMAP_API_KEY")
        city_id = self._weather_config_value(
            cfg,
            "location_code",
            "LocationCode",
            "city_id",
            "cityId",
            env="NSO_OPENWEATHER_CITY_ID",
        )
        if not city_id:
            city_id = self._weather_config_value(cfg, env="OPENWEATHERMAP_CITY_ID")
        if not api_key and not city_id:
            return {"available": False, "status": "missing_all"}
        if not api_key:
            return {"available": False, "status": "missing_api_key"}
        if not city_id:
            return {"available": False, "status": "missing_location"}
        cache = self.state.setdefault("weather_cache", {})
        now = time.time()
        refresh = int(cfg.get("refresh_minutes", 60)) * 60
        if cfg.get("UpdatesEvery"):
            try:
                refresh = int(cfg["UpdatesEvery"])
            except (TypeError, ValueError):
                pass
        cached_payload = cache.get("payload", {"available": False, "status": "cached-empty"})
        cache_age = now - float(cache.get("updated_at", 0) or 0)
        cache_ttl = refresh if cached_payload.get("status") == "ok" else min(refresh, WEATHER_ERROR_REFRESH_SECONDS)
        if cache.get("updated_at", 0) and cache_age < cache_ttl:
            return cached_payload
        units = self._weather_config_value(cfg, "units", "Units") or "metric"
        location_key = "id" if city_id.isdigit() else "q"
        query = urllib.parse.urlencode({location_key: city_id, "appid": api_key, "units": units})
        try:
            with urllib.request.urlopen(f"https://api.openweathermap.org/data/2.5/weather?{query}", timeout=4) as response:
                raw = json.loads(response.read().decode("utf-8"))
            weather = raw.get("weather", [{}])[0]
            unit = "F" if units == "imperial" else "K" if units == "standard" else "C"
            temp = raw.get("main", {}).get("temp")
            feels_like = raw.get("main", {}).get("feels_like")
            payload = {
                "available": True,
                "status": "ok",
                "city": raw.get("name", ""),
                "country": raw.get("sys", {}).get("country", ""),
                "temp": round(float(temp)) if temp is not None else "",
                "feels_like": round(float(feels_like)) if feels_like is not None else "",
                "description": weather.get("description", ""),
                "icon": weather.get("icon", ""),
                "unit": unit,
            }
        except Exception as exc:
            payload = {"available": False, "status": "error", "message": str(exc)}
        cache.update({"updated_at": now, "payload": payload})
        self.save()
        return payload

    def notes_text(self) -> str:
        try:
            return NOTES_FILE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def set_notes(self, title: str, body: str) -> None:
        NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        NOTES_FILE.write_text(body, encoding="utf-8")
        self.state.setdefault("notes", {})["title"] = title
        self.save()

    def accept_warning(self) -> None:
        self.state.setdefault("welcome", {})["warning_accepted"] = True
        self.save()

    def ame_action(self, action: str) -> None:
        ame = self.state.setdefault("ame", {})
        if action == "headpat":
            headpats = int(ame.get("headpats", 0)) + 1
            ame["headpats"] = headpats
            ame["love"] = min(100, int(ame.get("love", 20)) + 1)
            ame["darkness"] = max(0, int(ame.get("darkness", 0)) - 1)
            ame["headpat_dialogue"] = random.randrange(1, 5) if headpats % 3 == 0 else 1
            ame["last_headpat_at"] = time.time()
        elif action in {"idle", "game", "movie", "stream"}:
            ame["activity"] = action
        else:
            ame["activity"] = action
        self.save()


class ImageCache:
    def __init__(self) -> None:
        self.cache: dict[tuple[str, int | None, int | None], GdkPixbuf.Pixbuf] = {}

    def pixbuf(self, rel: str | Path, width: int | None = None, height: int | None = None) -> GdkPixbuf.Pixbuf | None:
        path = rel if isinstance(rel, Path) else IMAGES / rel
        key = (str(path), width, height)
        if key in self.cache:
            return self.cache[key]
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file(str(path))
        except GLib.Error:
            return None
        if width is not None or height is not None:
            width = width or pix.get_width()
            height = height or pix.get_height()
            pix = pix.scale_simple(width, height, GdkPixbuf.InterpType.NEAREST)
        self.cache[key] = pix
        return pix


class NsoWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, spec: WidgetSpec, model: NsoState) -> None:
        super().__init__(application=app, title=spec.title)
        self.spec = spec
        self.model = model
        self.images = ImageCache()
        self.system = model.system_state()
        self.mouse = (0.0, 0.0)
        self.click_regions: list[tuple[tuple[int, int, int, int], str]] = []
        self.edit_target: str | None = None
        self.ame_head_hover = False
        self.ame_menu_open = False
        self.ame_stream_prompt_open = False
        self.social_settings_open = False
        self.social_feedback: tuple[str, float] | None = None
        self.notes_title = str(model.state.get("notes", {}).get("title", "Quick Notes"))
        self.notes_body = model.notes_text()
        try:
            base_scale = float(model.config.get("ui", {}).get("scale", 1.5))
        except (TypeError, ValueError):
            base_scale = 1.5
        base_scale = max(0.75, min(3.0, base_scale))
        widget_scale = 1.0
        if spec.key == "ame":
            try:
                widget_scale = float(model.config.get("ame", {}).get("scale", 1.5))
            except (TypeError, ValueError):
                widget_scale = 1.5
            widget_scale = max(0.5, min(3.0, widget_scale))
        self.ui_scale = max(0.75, min(4.5, base_scale * widget_scale))
        self.logical_width = spec.width
        self.logical_height = spec.height
        self.refresh_content_size(force=True)
        pixel_width = int(round(self.logical_width * self.ui_scale))
        pixel_height = int(round(self.logical_height * self.ui_scale))
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(pixel_width, pixel_height)

        self.area = Gtk.DrawingArea()
        self.area.set_content_width(pixel_width)
        self.area.set_content_height(pixel_height)
        self.area.set_focusable(True)
        self.area.set_draw_func(self.on_draw)
        self.set_child(self.area)

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.area.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.on_motion)
        motion.connect("leave", self.on_leave)
        self.area.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.area.add_controller(keys)

        GLib.timeout_add(max(200, int(model.config.get("ame", {}).get("update_ms", 250))), self.tick)

    def tick(self) -> bool:
        self.system = self.model.system_state()
        self.refresh_content_size()
        self.area.queue_draw()
        return True

    def social_geometry(self) -> dict[str, Any]:
        tweet = self.model.current_tweet()
        image_w = 385
        source = self.images.pixbuf(f"Social Media/Tweets/{tweet['image']}") if tweet.get("image") else None
        if source:
            scaled_h = round(source.get_height() * image_w / max(1, source.get_width()))
            image_h = 440 if scaled_h >= 440 else 228
        else:
            image_h = 228
        return {
            "tweet": tweet,
            "source": source,
            "image_w": image_w,
            "image_h": image_h,
            "button_h": 25,
            "frame": f"Social Media/{image_h}.png",
            "window_w": 405,
            "window_h": image_h + 64,
        }

    def current_logical_size(self) -> tuple[int, int]:
        if self.spec.key == "social-media":
            geo = self.social_geometry()
            width, height = int(geo["window_w"]), int(geo["window_h"])
            if self.social_settings_open:
                width, height = max(width, 405), max(height, 365)
            return width, height
        if self.spec.key == "ame" and (self.ame_menu_open or self.ame_stream_prompt_open):
            menu_x, menu_y, menu_w, menu_h = self.ame_menu_rect()
            return max(self.spec.width, menu_x + menu_w), max(self.spec.height, menu_y + menu_h)
        return self.spec.width, self.spec.height

    def refresh_content_size(self, force: bool = False) -> None:
        logical_w, logical_h = self.current_logical_size()
        if not force and (logical_w, logical_h) == (self.logical_width, self.logical_height):
            return
        self.logical_width = logical_w
        self.logical_height = logical_h
        pixel_w = int(round(logical_w * self.ui_scale))
        pixel_h = int(round(logical_h * self.ui_scale))
        if hasattr(self, "area"):
            self.area.set_content_width(pixel_w)
            self.area.set_content_height(pixel_h)
            self.area.set_size_request(pixel_w, pixel_h)
        self.set_default_size(pixel_w, pixel_h)
        self.set_size_request(pixel_w, pixel_h)

    def on_motion(self, _controller: Gtk.EventControllerMotion, x: float, y: float) -> None:
        self.mouse = (x / self.ui_scale, y / self.ui_scale)
        if self.spec.key == "ame":
            overlay_open = self.ame_menu_open or self.ame_stream_prompt_open
            hovering = not overlay_open and self.point_in_rect(self.mouse[0], self.mouse[1], self.ame_headpat_rect())
            if hovering != self.ame_head_hover:
                self.ame_head_hover = hovering
                self.area.queue_draw()

    def on_leave(self, _controller: Gtk.EventControllerMotion) -> None:
        self.mouse = (-1.0, -1.0)
        if self.ame_head_hover:
            self.ame_head_hover = False
            self.area.queue_draw()

    def on_key(self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        name = Gdk.keyval_name(keyval) or ""
        if self.spec.key == "social-media":
            if name == "Escape" and self.social_settings_open:
                self.social_settings_open = False
                self.refresh_content_size()
                self.area.queue_draw()
                return True
            return False
        if self.spec.key == "ame":
            if name == "Escape":
                if self.ame_menu_open or self.ame_stream_prompt_open:
                    self.ame_menu_open = False
                    self.ame_stream_prompt_open = False
                    self.refresh_content_size()
                    self.area.queue_draw()
                    return True
                return False
            key_actions = {
                "h": "ame.headpat",
                "H": "ame.headpat",
                "a": "ame.menu",
                "A": "ame.menu",
                "1": "ame.activity:game",
                "2": "ame.activity:movie",
                "3": "ame.activity:stream",
                "4": "ame.activity:idle",
            }
            action = key_actions.get(name)
            if action:
                self.dispatch(action, 1)
                self.area.queue_draw()
                return True
            return False
        if self.spec.key != "quick-notes" or not self.edit_target:
            return False
        target = "notes_title" if self.edit_target == "title" else "notes_body"
        value = getattr(self, target)
        if name == "Escape":
            self.edit_target = None
        elif name == "BackSpace":
            value = value[:-1]
        elif name == "Return":
            value = value + ("\n" if target == "notes_body" else "")
            if target == "notes_title":
                self.edit_target = "body"
        elif name in {"Left", "Right", "Up", "Down", "Tab"}:
            return True
        else:
            char = chr(Gdk.keyval_to_unicode(keyval) or 0)
            if char and not (state & Gdk.ModifierType.CONTROL_MASK):
                value += char
        setattr(self, target, value)
        self.model.set_notes(self.notes_title, self.notes_body)
        self.area.queue_draw()
        return True

    def is_draggable_point(self, x: float, y: float) -> bool:
        if self.spec.key == "quick-notes" and 62 <= y <= 320:
            return False
        if self.spec.key == "ame":
            if (self.ame_menu_open or self.ame_stream_prompt_open) and self.point_in_rect(x, y, self.ame_menu_rect()):
                return False
            if self.point_in_rect(x, y, self.ame_headpat_rect()):
                return False
        for (rx, ry, rw, rh), _action in self.click_regions:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return False
        return y <= 40 or self.spec.key in {
            "task-manager",
            "ame",
            "jine",
            "social-media",
            "media-player",
            "calendar",
            "desktop-icons",
            "quick-notes",
            "medications",
            "welcome",
        }

    def begin_native_move(self, gesture: Gtk.GestureClick, button: int, x: float, y: float) -> bool:
        surface = self.get_surface()
        device = gesture.get_current_event_device()
        if button != 1 or surface is None or device is None or not hasattr(surface, "begin_move"):
            return False
        try:
            surface.begin_move(device, button, x, y, gesture.get_current_event_time())
        except Exception:
            return False
        return True

    def on_click(self, gesture: Gtk.GestureClick, _presses: int, x: float, y: float) -> None:
        self.area.grab_focus()
        raw_x, raw_y = x, y
        x /= self.ui_scale
        y /= self.ui_scale
        button = gesture.get_current_button()
        if self.spec.key == "quick-notes":
            self.edit_target = "title" if 62 <= y <= 90 else "body" if 96 <= y <= 320 else None
        for (rx, ry, rw, rh), action in reversed(self.click_regions):
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                self.dispatch(action, button)
                self.area.queue_draw()
                return
        if self.is_draggable_point(x, y) and self.begin_native_move(gesture, button, raw_x, raw_y):
            return

    def dispatch(self, action: str, button: int) -> None:
        if action == "close":
            self.close()
        elif action == "welcome.accept":
            self.model.accept_warning()
        elif action.startswith("launch:"):
            launch_widget(action.split(":", 1)[1])
        elif action.startswith("open:"):
            self.open_launcher(action.split(":", 1)[1])
        elif action.startswith("jine:"):
            self.model.jine_reply(action.split(":", 1)[1])
        elif action == "social.next":
            self.model.next_tweet()
            self.refresh_content_size()
        elif action == "social.settings":
            self.social_settings_open = not self.social_settings_open
            self.refresh_content_size()
        elif action == "social.settings.close":
            self.social_settings_open = False
            self.refresh_content_size()
        elif action.startswith("social.next-mode:"):
            self.model.set_social_next_mode(action.split(":", 1)[1])
        elif action.startswith("social.engage:"):
            kind = action.split(":", 1)[1]
            self.model.social_engage(kind)
            self.social_feedback = (kind, time.time() + 1.2)
        elif action.startswith("media:"):
            media_control(action.split(":", 1)[1], self.model.config)
        elif action == "ame.menu":
            self.ame_menu_open = not self.ame_menu_open
            self.ame_stream_prompt_open = False
            self.ame_head_hover = False
            self.refresh_content_size()
        elif action == "ame.menu.close":
            self.ame_menu_open = False
            self.ame_stream_prompt_open = False
            self.refresh_content_size()
        elif action.startswith("ame.activity:"):
            activity = action.split(":", 1)[1]
            if activity == "stream" and not self.model.ame_is_stream_time():
                self.ame_menu_open = False
                self.ame_stream_prompt_open = True
                self.refresh_content_size()
                return
            self.ame_menu_open = False
            self.ame_stream_prompt_open = False
            self.ame_head_hover = False
            self.refresh_content_size()
            self.model.ame_action(activity)
        elif action == "ame.stream.confirm":
            self.ame_menu_open = False
            self.ame_stream_prompt_open = False
            self.ame_head_hover = False
            self.refresh_content_size()
            self.model.ame_action("stream")
        elif action == "ame.stream.cancel":
            self.ame_menu_open = True
            self.ame_stream_prompt_open = False
            self.ame_head_hover = False
            self.refresh_content_size()
        elif action == "ame.headpat":
            self.model.ame_action("headpat")
        elif action == "ame.settings":
            self.ame_menu_open = False
            self.ame_stream_prompt_open = False
            self.refresh_content_size()
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(CONFIG_FILE)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif action == "meds":
            current = int(self.model.state.setdefault("meds", {}).get("spin", 0))
            self.model.state["meds"]["spin"] = current + 1
            self.model.save()

    def open_launcher(self, launcher_id: str) -> None:
        entries = {entry.get("id"): entry for entry in self.model.config.get("desktop_icons", [])}
        entry = entries.get(launcher_id, {})
        if entry.get("widget"):
            launch_widget(str(entry["widget"]))
            return
        target = entry.get("url") or entry.get("path")
        if target and shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", expand_config_value(str(target))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif entry.get("command"):
            subprocess.Popen(str(entry["command"]).split())

    def on_draw(self, _area: Gtk.DrawingArea, cr: Any, _width: int, _height: int) -> None:
        self.click_regions = []
        cr.set_source_rgb(*PINK)
        cr.paint()
        cr.save()
        if cairo is not None:
            cr.set_antialias(cairo.Antialias.NONE)
        cr.scale(self.ui_scale, self.ui_scale)
        getattr(self, f"draw_{self.spec.key.replace('-', '_')}")(cr)
        cr.restore()

    def region(self, rect: tuple[int, int, int, int], action: str) -> None:
        self.click_regions.append((rect, action))

    @staticmethod
    def point_in_rect(x: float, y: float, rect: tuple[int, int, int, int]) -> bool:
        rx, ry, rw, rh = rect
        return rx <= x <= rx + rw and ry <= y <= ry + rh

    @staticmethod
    def ame_headpat_rect() -> tuple[int, int, int, int]:
        return (110, 64, 148, 154)

    @staticmethod
    def ame_menu_rect() -> tuple[int, int, int, int]:
        return (55, 55, 508, 291)

    def draw_image(self, cr: Any, rel: str | Path, x: int, y: int, width: int | None = None, height: int | None = None) -> None:
        pix = self.images.pixbuf(rel, width, height)
        if pix is None:
            return
        Gdk.cairo_set_source_pixbuf(cr, pix, x, y)
        if cairo is not None:
            try:
                cr.get_source().set_filter(cairo.Filter.NEAREST)
            except Exception:
                pass
        cr.paint()

    def draw_image_fit_width(self, cr: Any, rel: str | Path, x: int, y: int, width: int, height: int) -> None:
        source = self.images.pixbuf(rel)
        if source is None:
            return
        scaled = self.images.pixbuf(rel, width, height)
        if scaled is None:
            return
        Gdk.cairo_set_source_pixbuf(cr, scaled, x, y)
        if cairo is not None:
            try:
                cr.get_source().set_filter(cairo.Filter.NEAREST)
            except Exception:
                pass
        cr.paint()

    def draw_image_cover(self, cr: Any, rel: str | Path, x: int, y: int, width: int, height: int) -> None:
        source = self.images.pixbuf(rel)
        if source is None:
            return
        scale = max(width / max(1, source.get_width()), height / max(1, source.get_height()))
        scaled_w = max(1, round(source.get_width() * scale))
        scaled_h = max(1, round(source.get_height() * scale))
        scaled = self.images.pixbuf(rel, scaled_w, scaled_h)
        if scaled is None:
            return
        offset_x = x + (width - scaled_w) // 2
        offset_y = y + (height - scaled_h) // 2
        cr.save()
        cr.rectangle(x, y, width, height)
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, scaled, offset_x, offset_y)
        if cairo is not None:
            try:
                cr.get_source().set_filter(cairo.Filter.NEAREST)
            except Exception:
                pass
        cr.paint()
        cr.restore()

    def draw_image_clipped(self, cr: Any, rel: str | Path, x: int, y: int, width: int, height: int) -> None:
        cr.save()
        cr.rectangle(x, y, width, height)
        cr.clip()
        self.draw_image(cr, rel, x, y, width, height)
        cr.restore()

    def make_text_layout(
        self,
        cr: Any,
        text: str,
        size: int = 12,
        width: int | None = None,
        font: str = "PixelMplus10",
        weight: str = "normal",
        align: str = "left",
        ellipsize: bool = True,
    ) -> Any:
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription(f"{font} {size}")
        if weight == "bold":
            desc.set_weight(Pango.Weight.BOLD)
        layout.set_font_description(desc)
        layout.set_text(text, -1)
        if width:
            layout.set_width(width * Pango.SCALE)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            if ellipsize:
                layout.set_ellipsize(Pango.EllipsizeMode.END)
            if align == "right":
                layout.set_alignment(Pango.Alignment.RIGHT)
            elif align == "center":
                layout.set_alignment(Pango.Alignment.CENTER)
        return layout

    def draw_text(
        self,
        cr: Any,
        text: str,
        x: int,
        y: int,
        size: int = 12,
        color: tuple[float, float, float] = PURPLE_DARK,
        width: int | None = None,
        font: str = "PixelMplus10",
        weight: str = "normal",
        align: str = "left",
        ellipsize: bool = True,
    ) -> None:
        layout = self.make_text_layout(cr, text, size, width, font, weight, align, ellipsize)
        if width is None and align == "right":
            text_width, _ = layout.get_pixel_size()
            x -= text_width
        elif width is None and align == "center":
            text_width, _ = layout.get_pixel_size()
            x -= text_width // 2
        cr.set_source_rgb(*color)
        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    def fill_rect(self, cr: Any, x: int, y: int, w: int, h: int, color: tuple[float, float, float]) -> None:
        cr.set_source_rgb(*color)
        cr.rectangle(x, y, w, h)
        cr.fill()

    def draw_close(self, cr: Any) -> None:
        if self.spec.width < 120:
            return
        x, y = CLOSE_POS.get(self.spec.key, (self.spec.width - 36, 11))
        self.draw_image(cr, "button_close.png", x, y)
        self.region((x, y, 20, 20), "close")

    def draw_frame(self, cr: Any, frame: str, title: str | None = None) -> None:
        self.draw_image(cr, frame, 0, 0)
        if title:
            self.draw_text(cr, title, 34, 14, 12, PURPLE, 250, "Dinkie Bitmap 7px")
        self.draw_close(cr)

    def draw_bar(self, cr: Any, x: int, y: int, w: int, h: int, pct: int, color: tuple[float, float, float]) -> None:
        self.fill_rect(cr, x, y, w, h, (240 / 255, 209 / 255, 241 / 255))
        self.fill_rect(cr, x, y, int(w * max(0, min(100, pct)) / 100), h, color)

    def fmt_time(self, seconds: int) -> str:
        seconds = max(0, seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours:
            return f"{hours}:{minutes:02d}:{seconds % 60:02d}"
        return f"{minutes}:{seconds % 60:02d}"

    @staticmethod
    def fmt_clock(now: datetime, hour24: bool) -> str:
        if hour24:
            return f"{now.hour:02d}:{now.minute:02d}"
        period = "AM" if now.hour < 12 else "PM"
        hour = now.hour % 12 or 12
        return f"{hour}:{now.minute:02d} {period}"

    def draw_task_manager(self, cr: Any) -> None:
        self.draw_frame(cr, "Task Manager/window.png", "Task Manager")
        cfg = self.model.config.get("task_manager", {})
        rows = [
            ("screen time", self.system["uptime"], "Task Manager/icon_status_follower.png", 61),
            ("cpu", f"{self.system['cpu']}%", "Task Manager/icon_status_stress.png", 136),
            ("memory", f"{self.system['memory']}%", "Task Manager/icon_status_love.png", 209),
            ("disk", f"{self.system['disk']}%", "Task Manager/icon_status_yami.png", 282),
        ]
        for label, value, icon, y in rows:
            self.draw_image(cr, icon, 28, y + 2, 36, 30)
            threshold_key = {"cpu": "cpu_critical", "memory": "memory_critical", "disk": "disk_critical"}.get(label, "")
            warning = bool(threshold_key and int(value.rstrip("%")) >= int(cfg.get(threshold_key, 90)))
            color = WARN if warning else PURPLE_DARK
            self.draw_text(cr, label.upper(), 78, y, 11, PURPLE, 140, "Dinkie Bitmap 7px")
            self.draw_text(cr, value, 240, y - 4, 22, color, 105, "PixelMplus10", "bold")
            if label in {"cpu", "memory", "disk"}:
                pct = int(value.rstrip("%"))
                self.draw_bar(cr, 78, y + 32, 264, 8, pct, color)
        self.draw_text(cr, "hover icons for status / gear opens config", 40, 316, 9, PURPLE, 320, "Dinkie Bitmap 7px")

    def draw_ame(self, cr: Any) -> None:
        state = self.model.ame_state(self.system, self.ame_head_hover)
        content_x, content_y, content_w, content_h = 8, 42, 348, 227
        self.draw_image(cr, "Ame/348.png", 0, 0)
        self.draw_image_clipped(cr, f"Ame/{state['background']}", content_x, content_y, content_w, content_h)
        self.draw_image_clipped(cr, f"Ame/{state['sprite']}", content_x, content_y, content_w, content_h)
        self.draw_text(cr, "Webcam", 34, 14, 12, PURPLE, 230, "Dinkie Bitmap 7px")
        self.draw_close(cr)
        self.draw_image(cr, "Ame/button_heart.png", 284, 11)
        self.draw_image(cr, "Ame/button_gear.png", 308, 11)
        self.region((284, 11, 20, 20), "ame.menu")
        self.region((308, 11, 20, 20), "ame.settings")
        if state["activity"] == "idle" and not state["streaming"]:
            self.region(self.ame_headpat_rect(), "ame.headpat")
        show_dialogue = self.ame_head_hover or time.time() - state["last_headpat_at"] < 2.0
        if show_dialogue and state["activity"] == "idle" and not state["streaming"]:
            dialogue = state["headpat_dialogue"] if time.time() - state["last_headpat_at"] < 2.0 else 0
            self.draw_image_clipped(cr, f"Ame/dialogue/{dialogue}.png", content_x, content_y, content_w, content_h)
        mood = "PISSED" if state["stress"] >= 80 else "STRESSED" if state["stress"] >= 55 else "NORMAL"
        self.draw_text(cr, mood, 24, 247, 10, WARN if state["stress"] >= 80 else PURPLE, 140, "Dinkie Bitmap 7px")
        self.draw_text(cr, f"LOVE {state['love']:02d}  DARK {state['darkness']:02d}", 24, 260, 8, PURPLE, 210, "Dinkie Bitmap 7px")
        if self.ame_menu_open:
            self.draw_ame_menu(cr, state)
        elif self.ame_stream_prompt_open:
            self.draw_ame_stream_prompt(cr)

    def draw_ame_menu(self, cr: Any, state: dict[str, Any]) -> None:
        menu_x, menu_y, menu_w, menu_h = self.ame_menu_rect()
        self.draw_image(cr, "Ame/menu/window.png", menu_x, menu_y)
        self.draw_text(cr, "Activities", menu_x + 34, menu_y + 14, 12, PURPLE, 220, "Dinkie Bitmap 7px")
        close_x, close_y = menu_x + menu_w - 34, menu_y + 11
        self.draw_image(cr, "button_close.png", close_x, close_y)
        self.region((close_x, close_y, 20, 20), "ame.menu.close")
        self.draw_text(cr, "Activities", menu_x + 130, menu_y + 82, 18, PURPLE, 240, "zpix")

        entries = [
            ("game", "game.png", "Game"),
            ("movie", "movie.png", "Movie"),
            ("stream", "youtube.png", "Stream"),
            ("idle", "ame.png", "Idle"),
        ]
        icon_y = menu_y + 130
        for index, (activity, icon, label) in enumerate(entries):
            icon_x = menu_x + 125 + index * 95
            if state["activity"] == activity:
                self.fill_rect(cr, icon_x - 5, icon_y - 5, 74, 74, (240 / 255, 209 / 255, 241 / 255))
            self.draw_image(cr, f"Ame/menu/{icon}", icon_x, icon_y)
            if activity == "stream" and not self.model.ame_is_stream_time():
                self.draw_text(cr, "?", icon_x + 49, icon_y + 1, 18, WARN, 28, "PixelMplus10", weight="bold")
            self.draw_text(cr, label, icon_x - 11, icon_y + 70, 12, PURPLE, 86, "Dinkie Bitmap 7px", align="center")
            self.region((icon_x - 5, icon_y - 5, 74, 94), f"ame.activity:{activity}")

    def draw_ame_stream_prompt(self, cr: Any) -> None:
        menu_x, menu_y, menu_w, _menu_h = self.ame_menu_rect()
        self.draw_image(cr, "Ame/menu/window_streamday.png", menu_x, menu_y)
        self.draw_text(cr, "Stream", menu_x + 34, menu_y + 14, 12, PURPLE, 220, "Dinkie Bitmap 7px")
        close_x, close_y = menu_x + menu_w - 34, menu_y + 11
        self.draw_image(cr, "button_close.png", close_x, close_y)
        self.region((close_x, close_y, 20, 20), "ame.stream.cancel")
        self.draw_text(cr, "Stream during daytime?", menu_x + 68, menu_y + 100, 15, PURPLE, 365, "PixelMplus10", align="center")

        yes_x, no_x, button_y = menu_x + 32, menu_x + 272, menu_y + 165
        self.draw_image(cr, "Ame/menu/button.png", yes_x, button_y)
        self.draw_image(cr, "Ame/menu/button.png", no_x, button_y)
        self.draw_text(cr, "Yes", yes_x + 100, button_y + 10, 14, PURPLE, None, "PixelMplus10", align="center")
        self.draw_text(cr, "No", no_x + 100, button_y + 10, 14, PURPLE, None, "PixelMplus10", align="center")
        self.region((yes_x, button_y, 200, 40), "ame.stream.confirm")
        self.region((no_x, button_y, 200, 40), "ame.stream.cancel")

    def draw_jine(self, cr: Any) -> None:
        self.draw_frame(cr, "JINE/window_new.png", "JINE")
        cr.save()
        cr.rectangle(8, 42, 300, 307)
        cr.clip()
        self.draw_image(cr, "JINE/JINEBGtiled.png", -4, -33)
        state = self.model.state.setdefault("jine", {})
        self.draw_image(cr, "JINE/scrollbar.png", 300, 54)
        self.draw_image(cr, "JINE/scrollbarslider.png", 300, 196)
        messages = self.model.dialogue.get("default", [])
        index = int(state.get("index", 0))
        last = state.get("last_reply") or (messages[index % len(messages)] if messages else "hey")
        max_text_width = 196
        natural_layout = self.make_text_layout(cr, str(last), 9, None, "zpix", ellipsize=False)
        natural_width, _natural_height = natural_layout.get_pixel_size()
        text_width = max(42, min(max_text_width, natural_width))
        layout_width = text_width if natural_width > max_text_width else None
        chat_layout = self.make_text_layout(cr, str(last), 9, layout_width, "zpix", ellipsize=False)
        measured_width, measured_height = chat_layout.get_pixel_size()
        bubble_w = max(74, min(228, measured_width + 24))
        bubble_h = max(42, min(88, measured_height + 17))
        ame_x, ame_y = 18, 66
        bubble_x, bubble_y = 46, 66
        self.draw_image(cr, "JINE/icon_jine_ame.png", ame_x, ame_y)
        self.draw_image(cr, "JINE/bubble-horiz.png", bubble_x, bubble_y, bubble_w, bubble_h)
        cr.save()
        cr.rectangle(bubble_x + 12, bubble_y + 4, bubble_w - 20, bubble_h - 8)
        cr.clip()
        cr.set_source_rgb(*PURPLE_DARK)
        cr.move_to(bubble_x + 12, bubble_y + 4)
        PangoCairo.show_layout(cr, chat_layout)
        cr.restore()
        last_sticker = str(state.get("last_sticker", ""))
        if last_sticker:
            user_y = min(164, bubble_y + bubble_h + 10)
            self.draw_image(cr, f"JINE/{last_sticker}.png", 234, user_y)
            self.draw_text(cr, "Read", 205, user_y + 45, 6, WHITE, None, "Dinkie Bitmap 7px")
            sent = max(0, int(time.time()) - int(state.get("last_sent", time.time())))
            self.draw_text(cr, f"last sent {sent}s ago", 298, 212, 8, (0, 0, 0), None, "zpix", align="right")
        self.draw_image(cr, "JINE/emoji_bgfull.png", 8, 226)
        stickers = self.model.config.get("jine", {}).get("stickers", ["ok", "omg", "sad", "idc", "sorry", "ded", "love", "this"])
        for i, name in enumerate(stickers[:8]):
            x = 40 + (i % 4) * 60
            y = 230 + (i // 4) * 60
            self.draw_image(cr, f"JINE/{name}.png", x, y)
            self.region((x, y, 56, 56), f"jine:{name}")
        cr.restore()

    def draw_social_media(self, cr: Any) -> None:
        geo = self.social_geometry()
        tweet = geo["tweet"]
        image_w = int(geo["image_w"])
        image_h = int(geo["image_h"])
        button_h = int(geo["button_h"])
        caption_h = 24
        engagement_h = 25
        image_body_h = max(80, image_h - caption_h - engagement_h)
        self.draw_frame(cr, str(geo["frame"]), "Social Media")
        user = str(tweet.get("user", "@x_angelkawaii_x"))
        icon = "Social Media/icon_cho.png" if user == "@x_angelkawaii_x" else "Social Media/icon_ame.png"
        if tweet.get("image"):
            self.draw_image_cover(cr, f"Social Media/Tweets/{tweet['image']}", 8, 42, image_w, image_body_h)
        else:
            self.draw_image(cr, "Social Media/nothing.png", 184, 42 + image_body_h // 2 - 16)
        self.fill_rect(cr, 8, 42, image_w, button_h, BLACK)
        self.draw_image(cr, icon, 11, 44, 20, 20)
        self.draw_text(cr, user, 36, 46, 10, WHITE, 235)

        caption_y = 42 + image_body_h
        engagement_y = caption_y + caption_h
        count_y = engagement_y + 5
        self.fill_rect(cr, 8, caption_y, image_w, caption_h, BLACK)
        body = str(tweet.get("text", ""))
        if body:
            self.draw_text(cr, body, 16, caption_y + 5, 10, WHITE, image_w - 16, "PixelMplus10", ellipsize=True)
        self.fill_rect(cr, 8, engagement_y, image_w, engagement_h, BLACK)
        self.region((8, engagement_y, image_w // 2, engagement_h), "social.engage:retweet")
        self.region((8 + image_w // 2, engagement_y, image_w - image_w // 2, engagement_h), "social.engage:like")
        retweet_x = round(405 * 0.22)
        like_x = round(405 * 0.60)
        self.draw_image(cr, "Social Media/icon_retweet.png", retweet_x, count_y, 14, 14)
        self.draw_text(cr, str(tweet.get("retweets", 0)), retweet_x + 17, count_y + 2, 9, (63 / 255, 155 / 255, 83 / 255), 90, "Press Start 2P")
        self.region((retweet_x - 8, engagement_y, 132, engagement_h), "social.engage:retweet")
        self.draw_image(cr, "Social Media/icon_star.png", like_x, count_y, 12, 12)
        self.draw_text(cr, str(tweet.get("likes", 0)), like_x + 15, count_y + 2, 9, (182 / 255, 179 / 255, 101 / 255), 90, "Press Start 2P")
        self.region((like_x - 8, engagement_y, 132, engagement_h), "social.engage:like")
        self.draw_image(cr, "Social Media/Settings/button_gear.png", 321, 11)
        self.draw_image(cr, "Social Media/button_right.png", 345, 11)
        self.region((317, 7, 28, 28), "social.settings")
        self.region((341, 7, 28, 28), "social.next")
        if self.social_feedback and self.social_feedback[1] > time.time():
            kind, _until = self.social_feedback
            label = "+1"
            x = retweet_x + 83 if kind == "retweet" else like_x + 72
            color = (63 / 255, 155 / 255, 83 / 255) if kind == "retweet" else (182 / 255, 179 / 255, 101 / 255)
            self.draw_text(cr, label, x, count_y + 2, 9, color, 30, "Dinkie Bitmap 7px")
        elif self.social_feedback:
            self.social_feedback = None
        if self.social_settings_open:
            self.draw_social_settings(cr)

    def draw_social_settings(self, cr: Any) -> None:
        self.draw_image(cr, "Social Media/Settings/window.png", 0, 0)
        self.draw_text(cr, "Social Media Settings", 34, 14, 12, PURPLE, 250, "Dinkie Bitmap 7px")
        self.draw_image(cr, "button_close.png", 369, 11)
        self.region((365, 7, 28, 28), "social.settings.close")
        self.draw_text(cr, "Next Button Action", 55, 76, 16, PURPLE_DARK, 295, "PixelMplus10")
        self.draw_image(cr, "Social Media/button_right.png", 59, 111)
        self.draw_text(cr, "Mode", 91, 112, 13, PURPLE, 180, "PixelMplus10")

        mode = self.model.social_next_mode()
        options = [("random", "Random", 70, 154), ("sequential", "Sequential", 70, 190)]
        for value, label, x, y in options:
            selected = mode == value
            self.draw_image(cr, f"Social Media/Settings/{'enabled' if selected else 'disabled'}.png", x, y)
            self.draw_text(cr, label, x + 32, y + 2, 13, PURPLE_DARK, 180, "PixelMplus10")
            self.region((x - 6, y - 6, 220, 33), f"social.next-mode:{value}")

        self.draw_text(cr, "Post Buttons", 55, 246, 16, PURPLE_DARK, 295, "PixelMplus10")
        self.draw_image(cr, "Social Media/icon_retweet.png", 72, 286, 14, 14)
        self.draw_text(cr, "Retweet", 104, 282, 13, PURPLE, 180, "PixelMplus10")
        self.draw_image(cr, "Social Media/icon_star.png", 234, 286, 12, 12)
        self.draw_text(cr, "Like", 264, 282, 13, PURPLE, 120, "PixelMplus10")

    def draw_media_player(self, cr: Any) -> None:
        self.draw_frame(cr, "Media Player/window.png", "Media Player")
        media = self.model.media_state()
        if media.get("special"):
            self.draw_image(cr, f"Media Player/{media['special']}", 8, 42)
        else:
            art_path = self.model.media_art_path(media.get("art", ""))
            self.draw_image(cr, art_path if art_path else "Media Player/nothing.png", 18, 53, 85, 85)
        self.draw_text(cr, media["title"], 112, 56, 15, PURPLE_DARK, 270, "PixelMplus10", "bold")
        self.draw_text(cr, media.get("artist", ""), 112, 83, 12, PURPLE, 270, "Dinkie Bitmap 7px")
        duration = int(media.get("duration", 0))
        position = int(media.get("position", 0))
        pct = int(position / duration * 100) if duration else 0
        pct = max(0, min(100, pct))
        self.draw_bar(cr, 110, 104, 270, 2, pct, PURPLE)
        duration_label = self.fmt_time(duration) if duration else "--:--"
        self.draw_text(cr, f"{self.fmt_time(position)}/{duration_label}", 380, 115, 12, PURPLE, None, "Dinkie Bitmap 7px", align="right")
        play_image = "Pause.png" if media.get("status") == "Playing" else "Play.png"
        controls = [("previous", "Previous.png", 112), ("play-pause", play_image, 147), ("next", "Next.png", 180)]
        for action, image, x in controls:
            self.draw_image(cr, f"Media Player/{image}", x, 115)
            self.region((x - 4, 111, 30, 30), f"media:{action}")

    def draw_calendar(self, cr: Any) -> None:
        self.draw_frame(cr, "Calendar/window.png", "Calendar")
        now = datetime.now()
        default_icon = "3.png" if now.hour > 18 else "2.png" if now.hour > 17 else "1.png" if now.hour > 5 else "3.png"
        weather = self.model.weather_state()
        icon = f"Calendar/Default/{default_icon}"
        if weather.get("available") and weather.get("icon"):
            weather_icon = f"Calendar/Icon/{weather.get('icon')}.png"
            if (IMAGES / weather_icon).exists():
                icon = weather_icon
        self.draw_image(cr, icon, 20, 64)
        hover = 0 <= self.mouse[0] <= self.spec.width and 0 <= self.mouse[1] <= self.spec.height
        if hover and weather.get("available"):
            temp = weather.get("temp")
            feels = weather.get("feels_like")
            unit = weather.get("unit", "C")
            location = ", ".join(part for part in [str(weather.get("city", "")), str(weather.get("country", ""))] if part)
            self.draw_text(cr, location, 95, 60, 23, PURPLE_DARK, 275, "PixelMplus10")
            self.draw_text(cr, f"{temp}{unit} feels like {feels}{unit}", 95, 90, 18, PURPLE_DARK, 275)
            self.draw_text(cr, str(weather.get("description", "")).title(), 95, 113, 10, PURPLE_DARK, 275)
        elif hover:
            status = str(weather.get("status", ""))
            if status == "disabled":
                primary = "Weather disabled"
                secondary = "set enabled=true"
            elif status == "missing_all":
                primary = "Missing weather setup"
                secondary = "set LocationCode and ApiKey"
            elif status == "missing_api_key":
                primary = "Missing ApiKey"
                secondary = "OpenWeatherMap key needed"
            elif status == "missing_location":
                primary = "Missing LocationCode"
                secondary = "use openweathermap city id"
            elif status == "error":
                primary = "Weather fetch failed"
                secondary = str(weather.get("message", ""))[:42]
            else:
                primary = "Weather not configured"
                secondary = "set LocationCode and ApiKey"
            self.draw_text(cr, primary, 95, 73, 14, PURPLE_DARK, 250)
            self.draw_text(cr, secondary, 95, 100, 9, PURPLE, 250, "Dinkie Bitmap 7px")
        else:
            self.draw_text(cr, f"{MONTH_NAMES[now.month - 1]} {now.day}, {now.year}", 95, 67, 23, PURPLE_DARK)
            calendar_cfg = self.model.config.get("calendar", {})
            time1224 = str(calendar_cfg.get("TimeFormat1224", calendar_cfg.get("time_format_1224", "")))
            hour24 = bool(calendar_cfg.get("hour24")) or time1224 == "24"
            self.draw_text(cr, self.fmt_clock(now, hour24), 95, 99, 18, PURPLE_DARK)

    def draw_desktop_icons(self, cr: Any) -> None:
        self.draw_frame(cr, "Desktop Icons/window_big.png", "Desktop Icons")
        for i, entry in enumerate(self.model.config.get("desktop_icons", [])[:12]):
            col = i % 4
            row = i // 4
            x = 31 + col * 88
            y = 58 + row * 91
            self.draw_image(cr, f"Desktop Icons/Icons/{entry.get('icon', 'ame.png')}", x, y, 48, 48)
            self.draw_text(cr, str(entry.get("label", "")), x - 8, y + 54, 8, PURPLE_DARK, 70, "Dinkie Bitmap 7px")
            self.region((x - 8, y - 6, 72, 76), f"open:{entry.get('id', '')}")

    def draw_quick_notes(self, cr: Any) -> None:
        variant = self.model.config.get("quick_notes", {}).get("variant", "large")
        frame = "Quick Notes/window_big_b.png" if variant == "large-black" else "Quick Notes/window_big.png"
        self.draw_frame(cr, frame, "Quick Notes")
        color = PURPLE_DARK
        self.draw_text(cr, self.notes_title + ("_" if self.edit_target == "title" else ""), 36, 64, 15, color, 310, "PixelMplus10", "bold")
        body = self.notes_body + ("_" if self.edit_target == "body" else "")
        self.draw_text(cr, body, 37, 103, 14, color, 320)
        self.draw_image(cr, "Quick Notes/button_minus.png", 325, 310)
        self.draw_image(cr, "Quick Notes/button_plus.png", 356, 310)

    def draw_sidebars_left(self, cr: Any) -> None:
        self.draw_image(cr, "Sidebars/1.png", 0, 0)

    def draw_sidebars_right(self, cr: Any) -> None:
        self.draw_image(cr, "Sidebars/2.png", 0, 0)

    def draw_medications(self, cr: Any) -> None:
        self.draw_frame(cr, "Medications/window.png", "Depaz")
        spin = int(self.model.state.setdefault("meds", {}).get("spin", 0))
        y_offset = 60 + (spin % 4) * 2
        self.draw_image(cr, "Medications/depaz_container.png", 29, y_offset)
        for x, y in [(70, 126), (10, 126), (70, 78), (10, 78), (70, 28), (10, 28)]:
            self.draw_image(cr, "Medications/depaz_drug.png", 29 + x, y_offset + y)
        self.region((29, y_offset, 118, 180), "meds")

    def draw_welcome(self, cr: Any) -> None:
        self.draw_frame(cr, "Welcome/window.png", "Welcome")
        accepted = bool(self.model.state.setdefault("welcome", {}).get("warning_accepted", False))
        if not accepted:
            self.draw_image(cr, "Welcome/smilie.png", 357, 92)
            self.draw_text(cr, "WARNING", 305, 210, 26, WARN, 190, "Press Start 2P")
            self.draw_text(cr, "This skin suite keeps the original NSO mature-theme warning.", 176, 265, 15, PURPLE_DARK, 440)
            self.draw_text(cr, "Click proceed to open the launcher dashboard.", 212, 318, 12, PURPLE, 360, "Dinkie Bitmap 7px")
            self.draw_image(cr, "Welcome/button.png", 310, 390)
            self.draw_text(cr, "PROCEED", 352, 405, 12, PURPLE_DARK, 110, "Dinkie Bitmap 7px")
            self.region((310, 390, 180, 55), "welcome.accept")
            return
        self.draw_text(cr, "NEEDY STREAMER OVERLOAD", 140, 70, 20, PURPLE_DARK, 540, "Press Start 2P")
        self.draw_text(cr, "native driftwm launcher", 280, 105, 11, PURPLE, 240, "Dinkie Bitmap 7px")
        launchers = [
            ("task-manager", "task.png", "Task"),
            ("ame", "ame.png", "Ame"),
            ("jine", "jine.png", "JINE"),
            ("social-media", "twitter.png", "Social"),
            ("media-player", "media.png", "Media"),
            ("calendar", "calendar.png", "Calendar"),
            ("desktop-icons", "text.png", "Icons"),
            ("quick-notes", "text.png", "Notes"),
            ("medications", "trash.png", "Meds"),
        ]
        for i, (skin, icon, label) in enumerate(launchers):
            col = i % 5
            row = i // 5
            x = 128 + col * 112
            y = 170 + row * 128
            self.draw_image(cr, f"Welcome/Icon/{icon}", x, y)
            self.draw_text(cr, label, x - 3, y + 72, 10, PURPLE_DARK, 82, "Dinkie Bitmap 7px")
            self.region((x - 12, y - 8, 88, 98), f"launch:{skin}")
        self.draw_text(cr, "All widgets are native Wayland windows; driftwm rules make them canvas widgets.", 117, 482, 12, PURPLE_DARK, 560)
        self.draw_text(cr, "Steam / GitHub links remain documented in extras/nso/README.md", 168, 520, 10, PURPLE, 470, "Dinkie Bitmap 7px")


def media_control(action: str, config: dict[str, Any]) -> None:
    if action not in {"play-pause", "next", "previous", "stop"} or not shutil.which("playerctl"):
        return
    command = ["playerctl"]
    player = config.get("media_player", {}).get("preferred_player", "")
    if player:
        command += ["--player", player]
    subprocess.Popen(command + [action], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def canonical_widget(name: str) -> str:
    name = ALIASES.get(name, name)
    if name not in SPECS:
        raise SystemExit(f"unknown NSO widget '{name}'. Known widgets: {', '.join(SPECS)}")
    return name


def launch_widget(name: str) -> None:
    key = canonical_widget(name)
    wrapper = shutil.which("driftwm-nso-widget")
    command = [wrapper, "--widget", key] if wrapper else [sys.executable, str(Path(__file__).resolve()), "--widget", key]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class NsoApplication(Gtk.Application):
    def __init__(self, spec: WidgetSpec, model: NsoState) -> None:
        super().__init__(application_id=spec.app_id)
        self.spec = spec
        self.model = model
        self.window: NsoWindow | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = NsoWindow(self, self.spec, self.model)
        self.window.present()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch native NSO driftwm widgets")
    parser.add_argument("--widget", default="welcome", help="widget key to launch")
    parser.add_argument("--list", action="store_true", help="list known widget keys")
    args = parser.parse_args(argv)
    if args.list:
        for key in LAUNCH_ORDER:
            print(key)
        return 0
    key = canonical_widget(args.widget)
    model = NsoState()
    app = NsoApplication(SPECS[key], model)
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(main())
