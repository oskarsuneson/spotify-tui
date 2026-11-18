#!/usr/bin/env python3
"""Terminal-Spotify: stapeldiagram, albumbild, låttext, kontroller."""
import argparse, colorsys, io, json, os, re, sys, threading, time

# Äkta färger (annars blir albumbilden grov och fel i många terminaler)
os.environ.setdefault("COLORTERM", "truecolor")
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")
from pathlib import Path

import numpy as np
import requests
import soundcard as sc
import spotipy
from PIL import Image, ImageFilter
from rich.text import Text
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyPKCE
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

THEMES = ["#00ff55", "#ffb000", "#00e5ff", "#ffffff", "#ff3df2", "#ff3b3b"]
BLOCKS = " ▁▂▃▄▅▆▇█"
BLOCK_ARR = np.array(list(BLOCKS))
NBARS = 48
UA = {"User-Agent": "terminal-spotify (personal project)"}
TS = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
TOKEN_CACHE = Path.home() / ".terminal-spotify-token"
ID_RE = re.compile(r"[0-9a-fA-F]{32}")


def config_path():
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "terminal-spotify" / "config.json"


def load_config():
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except Exception:  # noqa
        return {}


def save_config(cfg):
    try:
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


class Audio(threading.Thread):
    """Fångar systemljudet (WASAPI loopback / PulseAudio-PipeWire monitor) och gör FFT."""

    def __init__(self):
        super().__init__(daemon=True)
        self.levels = np.zeros(NBARS)
        self.error = None
        self.last_signal = time.time()

    def run(self):
        sr, n = 44100, 2048
        try:
            spk = sc.default_speaker()
            mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
            win = np.hanning(n)
            freqs = np.fft.rfftfreq(n, 1 / sr)
            edges = np.geomspace(40, 16000, NBARS + 1)
            idx = []
            for i in range(NBARS):
                a = int(np.searchsorted(freqs, edges[i]))
                b = max(int(np.searchsorted(freqs, edges[i + 1])), a + 1)
                idx.append((a, b))
            tilt = np.linspace(0.8, 1.7, NBARS)
            peak = 2.0
            with mic.recorder(samplerate=sr, channels=2, blocksize=n) as rec:
                while True:
                    data = rec.record(numframes=n).mean(axis=1)
                    spec = np.abs(np.fft.rfft(data * win))
                    vals = np.array([spec[a:b].mean() for a, b in idx])
                    vals = np.log1p(vals * 50) * tilt
                    peak = max(peak * 0.995, float(vals.max()), 2.0)
                    v = np.clip(vals / peak, 0, 1)
                    if v.max() > 0.05:
                        self.last_signal = time.time()
                    self.levels = np.maximum(v, self.levels * 0.82)
        except Exception as e:  # noqa
            self.error = f"Ljud: {e}"


class Player:
    def __init__(self, client_id):
        auth = SpotifyPKCE(
            client_id=client_id,
            redirect_uri="http://127.0.0.1:8888/callback",
            scope="user-read-playback-state user-modify-playback-state",
            cache_handler=CacheFileHandler(cache_path=str(Path.home() / ".terminal-spotify-token")),
            open_browser=True,
        )
        self.sp = spotipy.Spotify(auth_manager=auth)
        self.state, self.t, self.err = None, 0.0, None
        self.art_url, self.art_img = None, None
        self.lyr_id, self.lyrics = None, None  # lyrics: (typ, rader) | ("laddar", None)

    def login(self):
        self.sp.current_user()

    def fetch(self):
        s = self.sp.current_playback()
        self.state, self.t = s, time.time()
        item = s and s.get("item")
        if item:
            imgs = (item.get("album") or item).get("images") or []
            url = imgs[0]["url"] if imgs else None
            if url != self.art_url:
                self.art_url = url
                self.art_img = None
                if url:
                    raw = requests.get(url, timeout=10).content
                    self.art_img = Image.open(io.BytesIO(raw)).convert("RGB")

    def loop(self):
        while True:
            try:
                self.fetch()
                self.err = None
            except Exception as e:  # noqa
                self.err = str(e)[:80]
            time.sleep(1)

    # --- låttext (LRCLIB, gratis, ingen nyckel)
    def load_lyrics(self, item):
        tid = item.get("id") or item.get("name")
        if tid == self.lyr_id:
            return
        self.lyr_id, self.lyrics = tid, ("laddar", None)
        threading.Thread(target=self._lyrics, args=(item, tid), daemon=True).start()

    def _lyrics(self, item, tid):
        result = ("ingen", None)
        try:
            name = item["name"]
            artist = item["artists"][0]["name"]
            album = (item.get("album") or {}).get("name", "")
            dur = round(item["duration_ms"] / 1000)
            data = None
            r = requests.get("https://lrclib.net/api/get", headers=UA, timeout=10,
                             params={"track_name": name, "artist_name": artist,
                                     "album_name": album, "duration": dur})
            if r.status_code == 200:
                data = r.json()
            else:
                r = requests.get("https://lrclib.net/api/search", headers=UA, timeout=10,
                                 params={"track_name": name, "artist_name": artist})
                lst = r.json() if r.status_code == 200 else []
                data = lst[0] if lst else None
            if data:
                synced, plain = data.get("syncedLyrics"), data.get("plainLyrics")
                if synced:
                    lines = []
                    for raw in synced.splitlines():
                        stamps = TS.findall(raw)
                        text = TS.sub("", raw).strip()
                        for m, sec in stamps:
                            lines.append((int((int(m) * 60 + float(sec)) * 1000), text))
                    lines.sort(key=lambda x: x[0])
                    if lines:
                        result = ("synkad", lines)
                elif plain:
                    result = ("vanlig", plain.splitlines())
                elif data.get("instrumental"):
                    result = ("instrumental", None)
        except Exception:  # noqa
            pass
        if self.lyr_id == tid:
            self.lyrics = result

    def progress(self):
        s = self.state
        if not s or not s.get("item"):
            return 0, 0
        dur = s["item"]["duration_ms"]
        p = s.get("progress_ms") or 0
        if s.get("is_playing"):
            p += (time.time() - self.t) * 1000
        return min(p, dur), dur

    def _bg(self, fn, *a):
        def run():
            try:
                fn(*a)
                time.sleep(0.4)
                self.fetch()
            except Exception as e:  # noqa
                self.err = str(e)[:80]
        threading.Thread(target=run, daemon=True).start()

    def toggle(self):
        s = self.state
        playing = bool(s and s.get("is_playing"))
        if s:
            s["progress_ms"] = self.progress()[0]
            s["is_playing"] = not playing
            self.t = time.time()
        self._bg(self.sp.pause_playback if playing else self.sp.start_playback)

    def next(self):
        self._bg(self.sp.next_track)

    def prev(self):
        self._bg(self.sp.previous_track)

    def volume(self, d):
        dev = (self.state or {}).get("device")
        if not dev:
            return
        v = int(min(100, max(0, (dev.get("volume_percent") or 0) + d)))
        dev["volume_percent"] = v
        self._bg(self.sp.volume, v)

    def seek(self, delta_ms):
        s = self.state
        if not s or not s.get("item"):
            return
        p, d = self.progress()
        new = int(min(max(p + delta_ms, 0), max(d - 1000, 0)))
        s["progress_ms"], self.t = new, time.time()
        self._bg(self.sp.seek_track, new)


def art_text(img, w, h):
    img = img.resize((w, h * 2), Image.LANCZOS).filter(
        ImageFilter.UnsharpMask(radius=1, percent=70, threshold=2))
    px = img.load()
    t = Text(no_wrap=True)
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y * 2]
            r2, g2, b2 = px[x, y * 2 + 1]
            t.append("▀", style=f"rgb({r},{g},{b}) on rgb({r2},{g2},{b2})")
        if y < h - 1:
            t.append("\n")
    return t


def bars_text(levels, w, h, color):
    n = max(w // 2, 1)
    v = np.interp(np.linspace(0, len(levels) - 1, n), np.arange(len(levels)), levels)
    eighths = np.maximum((v * h * 8).astype(int), 1)
    rows = (h - 1 - np.arange(h))[:, None]  # översta raden först
    idx = np.clip(eighths[None, :] - rows * 8, 0, 8)
    mat = np.full((h, n * 2), " ", dtype="<U1")
    mat[:, ::2] = BLOCK_ARR[idx]
    return Text("\n".join("".join(r) for r in mat.tolist()), style=color, no_wrap=True)


def progress_bar(p, d, width):
    """Tunn förloppsbar över hela bredden: ━━━━●────"""
    pos = int(width * p / d) if d else 0
    pos = min(max(pos, 0), width - 1)
    return "━" * pos + "●" + "─" * (width - pos - 1)


def lyrics_text(lyr, p, d, w, h, color):
    t = Text(no_wrap=True, overflow="ellipsis")
    if not lyr or h < 1:
        return t
    kind, lines = lyr
    if kind == "laddar":
        t.append("Hämtar låttext...", style=f"dim {color}")
    elif kind == "ingen":
        t.append("Ingen låttext hittades.", style=f"dim {color}")
    elif kind == "instrumental":
        t.append("♪ Instrumental ♪", style=f"dim {color}")
    elif kind == "synkad":
        cur = -1
        for i, (ms, _) in enumerate(lines):
            if ms <= p + 200:
                cur = i
            else:
                break
        start = cur - h // 2
        for i in range(h):
            j = start + i
            if 0 <= j < len(lines):
                txt = lines[j][1] or "♪"
                if j == cur:
                    t.append("▶ " + txt, style=f"bold {color}")
                else:
                    t.append("  " + txt, style=f"dim {color}")
            if i < h - 1:
                t.append("\n")
    else:  # vanlig text utan tider: rullar ungefär efter förloppet
        extra = max(len(lines) - h, 0)
        start = int(extra * p / d) if d else 0
        for i in range(h):
            j = start + i
            if j < len(lines):
                t.append(lines[j], style=f"dim {color}")
            if i < h - 1:
                t.append("\n")
    return t


def accent_from(img):
    """Väljer en stark, tydlig färg som passar albumomslaget (hex)."""
    small = img.convert("RGB").resize((24, 24), Image.BILINEAR)
    px = small.load()
    bins = {}
    total = 0.0
    for y in range(24):
        for x in range(24):
            r, g, b = px[x, y]
            h, sat, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if v < 0.15:
                continue
            w = sat * sat * v
            total += w
            k = int(h * 24) % 24
            e = bins.setdefault(k, [0.0, 0.0, 0.0])
            e[0] += w
            e[1] += w * h
            e[2] += w * sat
    if not bins or total < 1.0:  # nästan svartvitt omslag
        return "#e0e0e0"
    wsum, hsum, ssum = max(bins.values(), key=lambda e: e[0])
    h, sat = hsum / wsum, ssum / wsum
    sat = min(1.0, max(sat, 0.55))
    r, g, b = colorsys.hsv_to_rgb(h, sat, 1.0)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def mmss(ms):
    s = int(ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


class Settings(ModalScreen):
    CSS = """
    Settings { align: center middle; background: black 70%; }
    #box { width: 72; max-width: 100%; height: auto; background: black; border: round #00ff55; padding: 1 2; }
    #box Static { margin-bottom: 1; }
    #box Input { width: 100%; margin-bottom: 1; }
    #box Button { width: 100%; margin-bottom: 1; }
    """
    BINDINGS = [("escape", "close", "Stäng")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("INSTÄLLNINGAR  (Esc = stäng, Tab = nästa fält)")
            yield Static("Spotify Client ID:")
            yield Input(value=self.app.cfg.get("client_id", ""), placeholder="klistra in ditt Client ID", id="cid")
            yield Static("", id="msg")
            yield Button("", id="b-color")
            yield Button("", id="b-img")
            yield Button("", id="b-lyr")
            yield Button("Spara och stäng", id="b-save")
            yield Button("Stäng", id="b-close")

    def on_mount(self):
        c = self.app.cur_color()
        self.query_one("#box").styles.border = ("round", c)
        for w in self.query("#box Static"):
            w.styles.color = c
        for w in self.query("Input, Button"):
            w.styles.color = c
            w.styles.background = "black"
            w.styles.border = ("round", c)
        self._labels()
        self.query_one("#cid", Input).focus()

    def _labels(self):
        a = self.app
        self.query_one("#b-color", Button).label = "Färg: " + ("auto (följer omslaget)" if a.auto else a.color)
        mode = "äkta bild (Sixel/Kitty)" if a.cfg.get("image_mode") == "graphics" else "block (fungerar överallt)"
        self.query_one("#b-img", Button).label = "Bildläge: " + mode
        self.query_one("#b-lyr", Button).label = "Låttext: " + ("på" if a.show_lyrics else "av")

    def _save_id(self):
        """Returnerar (ok, ändrat)."""
        a = self.app
        cid = self.query_one("#cid", Input).value.strip()
        if cid == a.cfg.get("client_id", ""):
            return True, False
        if not ID_RE.fullmatch(cid):
            self.query_one("#msg", Static).update("Ogiltigt Client ID (32 tecken: 0-9 och a-f).")
            return False, False
        a.cfg["client_id"] = cid
        save_config(a.cfg)
        try:
            TOKEN_CACHE.unlink()
        except OSError:
            pass
        self.query_one("#msg", Static).update("Sparat! Starta om appen så används det nya ID:t.")
        return True, True

    def on_input_submitted(self, ev):
        self._save_id()

    def on_button_pressed(self, ev):
        a, bid = self.app, ev.button.id
        if bid == "b-color":
            a.action_color()
        elif bid == "b-img":
            a.cfg["image_mode"] = "blocks" if a.cfg.get("image_mode") == "graphics" else "graphics"
            save_config(a.cfg)
            self.query_one("#msg", Static).update("Bildläget ändras när du startar om appen.")
        elif bid == "b-lyr":
            a.action_lyrics()
        elif bid == "b-save":
            ok, changed = self._save_id()
            if ok and not changed:
                self.dismiss()
        elif bid == "b-close":
            self.dismiss()
        self._labels()

    def action_close(self):
        self.dismiss()


class MusicApp(App):
    CSS = """
    Screen { background: black; }
    #top { height: 16; }
    #art { width: 34; height: 16; }
    #info { width: 1fr; height: 100%; padding: 1 3; }
    #lyrics { width: 45%; min-width: 30; height: 100%; padding: 0 3 0 4; }
    #prog { height: 1; padding: 0 1; }
    #bars { height: 1fr; }
    #help { height: 1; }
    """
    BINDINGS = [
        Binding("space", "toggle", "Start/Stopp"),
        Binding("n", "next", "Nästa"),
        Binding("p", "prev", "Förra"),
        Binding("up", "volume(5)", "Ljud +"),
        Binding("down", "volume(-5)", "Ljud -"),
        Binding("right", "seek(10000)", "Spola +"),
        Binding("left", "seek(-10000)", "Spola -"),
        Binding("c", "color", "Färg"),
        Binding("l", "lyrics", "Låttext"),
        Binding("s", "settings", "Inställningar"),
        Binding("q", "quit", "Avsluta"),
    ]

    def __init__(self, player, audio, color, cfg, gimage=None):
        super().__init__()
        self.P, self.A = player, audio
        self.auto = color == "auto"
        self.color = THEMES[0] if self.auto else color
        self.accent, self.accent_url = None, None
        self.art_cache = (None, None)
        self.art_h = 0
        self.cfg, self.gimage, self.ui_err = cfg, gimage, None
        self.show_lyrics = cfg.get("lyrics", True)
        self.n = 0
        self._last = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            art = None
            if self.gimage:
                try:
                    art = self.gimage(id="art")
                except Exception:  # noqa
                    self.gimage = None
            yield art if art is not None else Static(id="art")
            yield Static(id="info")
            yield Static(id="lyrics")
        yield Static(id="prog")
        yield Static(id="bars")
        yield Static(id="help")

    def on_mount(self):
        threading.Thread(target=self.P.loop, daemon=True).start()
        self.A.start()
        self.w = {i: self.query_one("#" + i) for i in ("top", "art", "info", "lyrics", "prog", "bars", "help")}
        self.bars_w = self.w["bars"]
        self.set_interval(1 / 25, self.tick)

    # --- actions
    def action_toggle(self): self.P.toggle()
    def action_next(self): self.P.next()
    def action_prev(self): self.P.prev()
    def action_volume(self, d: int): self.P.volume(d)
    def action_seek(self, ms: int): self.P.seek(ms)
    def action_lyrics(self):
        self.show_lyrics = not self.show_lyrics
        self.cfg["lyrics"] = self.show_lyrics
        save_config(self.cfg)

    def action_settings(self):
        if not isinstance(self.screen, Settings):
            self.push_screen(Settings())

    def action_color(self):
        opts = ["auto"] + THEMES
        cur = "auto" if self.auto else self.color
        i = opts.index(cur) if cur in opts else -1
        nxt = opts[(i + 1) % len(opts)]
        self.auto = nxt == "auto"
        if not self.auto:
            self.color = nxt
        self.cfg["color"] = nxt
        save_config(self.cfg)

    # --- ritning
    def cur_color(self):
        return self.accent if (self.auto and self.accent) else self.color

    def _set(self, wid, content, c, key=None):
        k = (c, key if key is not None else content)
        if self._last.get(wid) != k:
            self._last[wid] = k
            self.w[wid].update(content)

    def tick(self):
        self.n += 1
        P = self.P
        if P.art_img is not None and self.accent_url != P.art_url:
            self.accent_url = P.art_url
            self.accent = accent_from(P.art_img)
        c = self.cur_color()
        try:
            if self.n % 5 == 1:
                self.slow(c)
            b = self.bars_w
            if b.size.width > 2 and b.size.height > 0:
                b.update(bars_text(self.A.levels, b.size.width, b.size.height, c))
        except Exception as e:  # noqa
            self.ui_err = f"Visning: {e!r}"[:100]

    def slow(self, c):
        P = self.P
        s = P.state
        item = s and s.get("item")
        p, d = P.progress()

        # albumbild (storleken anpassas efter terminalens höjd)
        ah = max(12, min(44, int(self.size.height * 0.6)))
        if ah != self.art_h:
            self.art_h = ah
            self.w["top"].styles.height = ah
            self.w["art"].styles.width = ah * 2 + 2
            self.w["art"].styles.height = ah
        if self.gimage:  # äkta bild (Sixel/Kitty)
            if P.art_img is not None and self._last.get("gimg") != P.art_url:
                self._last["gimg"] = P.art_url
                self.w["art"].image = P.art_img
        else:  # färgblock
            key = (P.art_url, ah)
            if P.art_img is not None and self.art_cache[0] != key:
                self.art_cache = (key, art_text(P.art_img, ah * 2, ah))
            if P.art_img is not None and self.art_cache[1] is not None:
                self._set("art", self.art_cache[1], "", key=self.art_cache[0])
            else:
                self._set("art", Text(""), "")

        # info
        info = Text(no_wrap=True, overflow="ellipsis")
        if item:
            P.load_lyrics(item)
            album = (item.get("album") or {}).get("name", "")
            artists = ", ".join(a["name"] for a in item.get("artists", [])) or item.get("show", {}).get("name", "")
            playing = s.get("is_playing")
            vol = ((s.get("device") or {}).get("volume_percent")) or 0
            vfill = int(vol / 100 * 12)
            info.append(item["name"] + "\n", style=f"bold {c}")
            info.append(artists + "\n", style=c)
            info.append(album + "\n\n", style=f"dim {c}")
            info.append(("▶ SPELAR" if playing else "⏸ PAUSAD") + "\n", style=f"bold {c}")
            info.append("LJUD " + "█" * vfill + "░" * (12 - vfill) + f" {vol}%", style=c)
        else:
            info.append("Ingen uppspelning.\nStarta Spotify på någon enhet.", style=c)
        self._set("info", info, c)

        # låttext
        ly = self.w["lyrics"]
        if self.show_lyrics and item:
            self._set("lyrics", lyrics_text(P.lyrics, p, d, ly.size.width, ly.size.height, c), c)
        else:
            self._set("lyrics", Text(""), c)

        # förloppsbar över hela fönsterbredden
        pr = self.w["prog"]
        if item and pr.size.width > 12:
            left, right = f"{mmss(p)} ", f" {mmss(d)}"
            bw = max(pr.size.width - len(left) - len(right), 4)
            self._set("prog", Text(left + progress_bar(p, d, bw) + right, style=c, no_wrap=True), c)
        else:
            self._set("prog", Text(""), c)

        # hjälprad
        msg = self.A.error or P.err or self.ui_err
        quiet = (not msg and s and s.get("is_playing")
                 and time.time() - self.A.last_signal > 4)
        help_t = Text(no_wrap=True, overflow="ellipsis")
        if msg:
            help_t.append(msg, style="bold red")
        elif quiet:
            help_t.append("Inget ljud fångas - bara ljud som spelas inuti Linux kan visas i staplarna", style="bold yellow")
        else:
            help_t.append("[mellanslag] start/stopp  [n] nästa  [p] förra  [↑↓] ljud  [←→] spola  [l] text  [c] färg  [s] inställningar  [q] avsluta", style=f"dim {c}")
        self._set("help", help_t, c)

def ask_client_id(cfg):
    print("Första gången behöver du ett eget Spotify Client ID (gratis, kräver Premium).")
    print("1. Gå till https://developer.spotify.com/dashboard och välj Create app.")
    print("2. Redirect URI: http://127.0.0.1:8888/callback  (kryssa i Web API)")
    print("3. Öppna Settings i appen, kopiera Client ID och klistra in det här.")
    cid = input("Client ID: ").strip()
    if not ID_RE.fullmatch(cid):
        sys.exit("Det där ser inte ut som ett Client ID (ska vara 32 tecken, 0-9 och a-f).")
    cfg["client_id"] = cid
    save_config(cfg)
    return cid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", help="Spotify client ID (annars används inställningarna eller SPOTIFY_CLIENT_ID)")
    ap.add_argument("--color", default=None, help="auto (följer albumomslaget) eller hex-färg, t.ex. #00ff55")
    args = ap.parse_args()
    cfg = load_config()
    cid = args.client_id or cfg.get("client_id") or os.environ.get("SPOTIFY_CLIENT_ID")
    if not cid:
        cid = ask_client_id(cfg)
    color = args.color or cfg.get("color", "auto")
    gimage = None
    if cfg.get("image_mode") == "graphics":
        try:
            from textual_image.widget import Image as gimage
        except Exception:  # noqa
            print("Paketet textual-image saknas (pip install textual-image). Använder blockbild.")
            gimage = None
    player = Player(cid)
    print("Loggar in på Spotify (en webbläsare öppnas första gången)...")
    player.login()
    MusicApp(player, Audio(), color, cfg, gimage).run()


if __name__ == "__main__":
    main()
