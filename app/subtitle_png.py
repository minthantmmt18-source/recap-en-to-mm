"""Recap narration -> shaped, transparent PNG cues and one sparse FFmpeg image stream.

The subtitle tool's font/outline/shadow/panel controls are retained. Chromium shapes Myanmar text;
one browser renders all cues. Long narration is split into two-line cards on the actual voice clock.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import re
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from subtitle_fonts import myanmar_fonts

DEFAULTS = dict(enabled=True, font="Myanmar Text", size=48, bold=False, color="#FFFFFF",
                outline_color="#252525", outline_width=-1, shadow=0, box_enabled=True,
                box_color="#383838", box_opacity=62, width=0.86, position=[0.5, 0.92])
SAMPLE = "ကျွန်တော် အိမ်ပြန်လမ်းမှာ တစ်ယောက်တည်း လမ်းလျှောက်လာခဲ့တယ်။"


@lru_cache(maxsize=1)
def _browser_launcher():
    # Load this tool's own renderer by its exact path (another "services" package may be on sys.path).
    path = Path(__file__).resolve().parents[1] / "services" / "text_overlay_service.py"
    spec = spec_from_file_location("recap_text_overlay_service", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._browser_candidates


def style(values: dict | None = None) -> dict:
    s = {**DEFAULTS, **(values or {})}
    s["size"] = max(12, min(144, int(s["size"])))
    s["width"] = max(.2, min(1., float(s["width"])))
    s["outline_width"] = max(-1, min(12, int(s["outline_width"])))
    s["shadow"] = max(0, min(12, int(s["shadow"])))
    s["box_opacity"] = max(0, min(100, int(s["box_opacity"])))
    s["position"] = [max(0., min(1., float(v))) for v in s["position"][:2]]
    if len(s["position"]) != 2:
        s["position"] = list(DEFAULTS["position"])
    for key in ("color", "outline_color", "box_color"):
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(s[key])):
            raise ValueError(f"Subtitle {key}: #RRGGBB လို ရေးပါ။")
    return s


def scaled(values: dict, k: float) -> dict:
    """The style for a smaller frame (720p / 480p video quality): pixel sizes times k, places and widths as they are,
    so the subtitle looks the same as on the 1080 frame it was set up on."""
    s = style(values)
    if k >= 0.999:
        return s
    return style({**s, "size": max(12, round(s["size"] * k)), "shadow": round(s["shadow"] * k),
                  "outline_width": s["outline_width"] if s["outline_width"] < 0 else round(s["outline_width"] * k)})


def geometry(s: dict, frame: tuple[int, int]) -> tuple[int, int, int, int]:
    fw, fh = frame
    width = max(8, round(fw * s["width"]))
    height = min(fh, math.ceil(s["size"] * 3.4 + 2 * max(s["shadow"], s["outline_width"], 4)))
    x = max(0, min(fw - width, round(fw * s["position"][0] - width / 2)))
    y = max(0, min(fh - height, round(fh * s["position"][1] - height)))
    return width, height, x, y


def _clean(text: str) -> str:
    text = re.sub(r"\s*\([A-Za-z0-9 ,.'\-]+\)", "", str(text))
    return re.sub(r"\s+", " ", text).strip()


def _fmt(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def write_srt(cues: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(f"{i}\n{_fmt(c['start'])} --> {_fmt(c['end'])}\n{c['text']}"
                                  for i, c in enumerate(cues, 1)) + "\n", encoding="utf-8")
    return path


def upload_srt(final_video: Path, segments: list[dict], burned_srt: str | Path | None = None) -> Path:
    """The SRT for uploading next to a finished video: <video folder>/SRT_for_upload/<video name>.srt — not beside the
    video, where VLC / PotPlayer / MX Player load a same-name .srt and show every line twice (RECAP tool, 2026-10-12).
    Burned subtitles: the same two-line cards as on the picture; otherwise one cue per narration line."""
    final_video = Path(final_video)
    target = final_video.parent / "SRT_for_upload" / f"{final_video.stem}.srt"
    if burned_srt and Path(burned_srt).is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(burned_srt).read_bytes())
        return target
    cues = [dict(start=float(s["start"]), end=float(s["end"]), text=_clean(s.get("text", "")))
            for s in segments if float(s["end"]) > float(s["start"]) and _clean(s.get("text", ""))]
    return write_srt(cues, target)


def write_timeline(cues: list[dict], blank: Path, path: Path, duration: float) -> Path:
    """One PNG packet at each cue/gap boundary, with millisecond timestamps and explicit transparent gaps."""
    lines = ["ffconcat version 1.0"]

    def add(png: Path, seconds: float):
        if seconds <= 0:
            return
        name = png.name.replace("'", "'\\''")
        lines.extend([f"file '{name}'", "option framerate 1000", f"duration {seconds:.6f}"])

    cursor = 0.
    for cue in cues:
        start = max(cursor, min(duration, float(cue["start"])))
        end = min(duration, float(cue["end"]))
        if end <= start:
            continue
        add(blank, start - cursor)
        add(Path(cue["png"]), end - start)
        cursor = end
    add(blank, duration - cursor)
    lines.extend([f"file '{blank.name}'", "option framerate 1000"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class Renderer:
    def __init__(self, values: dict, frame: tuple[int, int], folder: Path):
        self.s, self.frame, self.folder = style(values), frame, folder
        self.width, self.height, self.x, self.y = geometry(self.s, frame)
        self.folder.mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        candidates = _browser_launcher()
        self.pw = sync_playwright().start()
        self.browser = None
        try:
            for _label, launch in candidates(self.pw):
                try:
                    self.browser = launch()
                    break
                except Exception:
                    continue
            if self.browser is None:
                raise RuntimeError("Subtitle PNG ထုတ်ဖို့ Chromium / Chrome / Edge မတွေ့ပါ။")
            self.page = self.browser.new_page(viewport={"width": self.width, "height": self.height},
                                              device_scale_factor=1)
            s = self.s
            font_files = myanmar_fonts().get(s["font"], [])
            font_css = ""
            family = html.escape(s["font"], quote=True)
            if font_files:
                chosen = next((p for p in font_files if ("bold" in p.lower()) == bool(s["bold"])), font_files[0])
                data = base64.b64encode(Path(chosen).read_bytes()).decode()
                font_css = f"@font-face{{font-family:SubtitleFont;src:url(data:font/ttf;base64,{data})}}"
                family = "SubtitleFont"
            stroke = max(2, round(s["size"] * .075)) if s["outline_width"] < 0 else s["outline_width"]
            pad = max(3, math.ceil(s["size"] * .2), stroke + s["shadow"])
            rgb = tuple(int(s["box_color"][i:i+2], 16) for i in (1, 3, 5))
            panel = f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{s['box_opacity']/100})" if s["box_enabled"] else "transparent"
            shadow = f"{s['shadow']}px {s['shadow']}px 1px #000" if s["shadow"] else "none"
            self.page.set_content(f"""<style>{font_css}
html,body{{margin:0;background:transparent;width:100%;height:100%;overflow:hidden}}
#card{{position:absolute;bottom:0;left:0;width:100%;box-sizing:border-box;padding:{pad}px;
background:{panel};text-align:center}}
#text{{font-family:'{family}','Myanmar Text','Noto Sans Myanmar','Padauk',sans-serif;font-size:{s['size']}px;
font-weight:{700 if s['bold'] else 400};line-height:1.45;white-space:pre-wrap;
color:{s['color']};-webkit-text-stroke:{stroke}px {s['outline_color']};paint-order:stroke fill;
text-shadow:{shadow}}}</style><div id=card><div id=text></div></div>""")
            self.page.evaluate("() => document.fonts.ready")
            self.signature = json.dumps({"style": s, "frame": list(self.frame), "renderer": 1}, sort_keys=True)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_args):
        if self.browser:
            self.browser.close()
        self.pw.stop()

    def cards(self, text: str) -> list[str]:
        """Measure shaped words with the same browser/font as the PNG; never break combining marks."""
        return self.page.evaluate(r"""text => {
const el=document.querySelector('#text'), css=getComputedStyle(el), ctx=document.createElement('canvas').getContext('2d');
ctx.font=`${css.fontWeight} ${css.fontSize} ${css.fontFamily}`;
const limit=el.clientWidth, lines=[]; let line='';
const tokens=Array.from(new Intl.Segmenter('my',{granularity:'word'}).segment(text), x=>x.segment);
for (const token of tokens) {
 if (ctx.measureText(line+token).width<=limit) {line+=token;continue;}
 if(line.trim()){lines.push(line.trim());line='';}
 if(ctx.measureText(token).width<=limit){line=token.trimStart();continue;}
 for(const {segment} of new Intl.Segmenter('my',{granularity:'grapheme'}).segment(token)) {
  if(line && ctx.measureText(line+segment).width>limit){lines.push(line.trim());line='';}
  line+=segment;
 }
}
if(line.trim())lines.push(line.trim());
const cards=[];for(let i=0;i<lines.length;i+=2)cards.push(lines.slice(i,i+2).join('\n'));
return cards;
}""", _clean(text))

    def png(self, text: str) -> Path:
        key = hashlib.sha256((self.signature + text).encode()).hexdigest()[:24]
        path = self.folder / f"cue_{key}.png"
        if not path.is_file():
            self.page.evaluate("text => {document.querySelector('#text').textContent=text}", text)
            self.page.locator("#card").evaluate("(el, blank) => {el.style.visibility=blank?'hidden':'visible'}", not text)
            self.page.screenshot(path=str(path), omit_background=True, animations="disabled")
        return path

    def prepare(self, segments: list[dict], *, should_stop=lambda: False, log=print) -> list[dict]:
        cues = []
        for index, seg in enumerate(segments):
            if should_stop():
                raise InterruptedError("STOP requested during subtitle PNG rendering")
            start, end = float(seg["start"]), float(seg["end"])
            if end <= start:
                continue
            cards = self.cards(seg.get("text", ""))
            weights = [max(1, len(re.sub(r"\s", "", card))) for card in cards]
            total, elapsed = sum(weights), 0
            for card, weight in zip(cards, weights):
                a = start + (end - start) * elapsed / total
                elapsed += weight
                b = end if elapsed == total else start + (end - start) * elapsed / total
                cues.append(dict(start=a, end=b, text=card, png=str(self.png(card))))
            if index % 25 == 0:
                log(f"📝 Subtitle PNG {index+1}/{len(segments)}")
        return cues


def prepare(segments: list[dict], values: dict, frame: tuple[int, int], folder: Path, duration: float,
            *, should_stop=lambda: False, log=print) -> dict:
    with Renderer(values, frame, folder) as renderer:
        cues = renderer.prepare(segments, should_stop=should_stop, log=log)
        blank = renderer.png("")
        timeline = write_timeline(cues, blank, folder / "subtitles.ffconcat", duration)
        srt = write_srt(cues, folder / "narration.srt")
        return dict(timeline=str(timeline), srt=str(srt), x=renderer.x, y=renderer.y, cues=cues)


def sample(values: dict, frame: tuple[int, int], folder: Path) -> Path:
    with Renderer(values, frame, folder) as renderer:
        cards = renderer.cards(SAMPLE)
        return renderer.png(cards[0] if cards else SAMPLE)
