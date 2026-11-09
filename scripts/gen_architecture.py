"""Generate docs/assets/architecture-{light,dark}.svg for the README.

Usage: python scripts/gen_architecture.py docs/assets
"""

import sys
from pathlib import Path

OUT = Path(sys.argv[1])
W, H = 1120, 640
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace"

THEMES = {
    "light": dict(
        bg="#ffffff",
        panel="#f6f8fa",
        stroke="#d0d7de",
        card="#ffffff",
        text="#1f2328",
        muted="#59636e",
        accent="#6366f1",
        accent_bg="#eef2ff",
        accent_text="#3730a3",
        warn="#bf8700",
        warn_bg="#fff8c5",
        ok="#1a7f37",
        ok_bg="#dafbe1",
        arrow="#8c959f",
    ),
    "dark": dict(
        bg="#0d1117",
        panel="#151b23",
        stroke="#3d444d",
        card="#0d1117",
        text="#f0f6fc",
        muted="#9198a1",
        accent="#818cf8",
        accent_bg="#1e1b4b",
        accent_text="#c7d2fe",
        warn="#d29922",
        warn_bg="#2e2410",
        ok="#3fb950",
        ok_bg="#12261a",
        arrow="#656c76",
    ),
}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build(t):
    o = []
    a = o.append

    def rect(x, y, w, h, fill, stroke, r=12, sw=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        a(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"{d}/>'
        )

    def text(x, y, s, size=14, color=None, weight=400, mono=False, anchor="start", ls=None):
        fam = MONO if mono else FONT
        spacing = f' letter-spacing="{ls}"' if ls else ""
        a(
            f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" font-weight="{weight}" '
            f'fill="{color or t["text"]}" text-anchor="{anchor}"{spacing}>{esc(s)}</text>'
        )

    def label(x, y, s):
        text(x, y, s, 11, t["muted"], 600, ls="1.2")

    def card(x, y, w, h, title, sub, mono_title=True, fill=None, stroke=None, tcolor=None):
        rect(x, y, w, h, fill or t["card"], stroke or t["stroke"], 10)
        text(x + 16, y + 30, title, 15, tcolor or t["text"], 600, mono=mono_title)
        for i, line in enumerate(sub if isinstance(sub, list) else [sub]):
            text(x + 16, y + 52 + i * 18, line, 12.5, t["muted"])

    def arrow(x1, y1, x2, y2, lab=None, lx=None, ly=None, both=False):
        start = ' marker-start="url(#ah)"' if both else ""
        a(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{t["arrow"]}" stroke-width="1.6"'
            f' marker-end="url(#ah)"{start}/>'
        )
        if lab:
            text(lx, ly, lab, 11.5, t["muted"], 500, mono=True)

    a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
    a(
        '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{t["arrow"]}"/></marker></defs>'
    )
    rect(0, 0, W, H, t["bg"], t["bg"], 16)

    # --- top row: human -> CLI -> orchestrator
    rect(32, 32, 208, 84, t["card"], t["stroke"], 10)
    text(48, 58, "You", 15, t["text"], 600)
    text(82, 58, "in the terminal", 12.5, t["muted"])
    for i, (word, col, bg) in enumerate(
        [
            ("approve", t["ok"], t["ok_bg"]),
            ("edit", t["warn"], t["warn_bg"]),
            ("reject", "#cf222e" if t is THEMES["light"] else "#f85149", t["panel"]),
        ]
    ):
        bx = 48 + i * 62
        rect(bx, 76, 56, 22, bg, col, 11)
        text(bx + 28, 91, word, 11, col, 600, anchor="middle")
    card(
        288, 32, 220, 84, "shipwright run", ["main.py: streams the run,", "resumes paused threads"]
    )
    rect(556, 32, 532, 84, t["accent_bg"], t["accent"], 12, 1.5)
    text(576, 62, "Orchestrator", 16, t["accent_text"], 700)
    text(684, 62, "create_deep_agent(...)", 13, t["accent_text"], 500, mono=True)
    text(
        576, 86, "reads the ticket · plans with write_todos · delegates with task", 12.5, t["muted"]
    )
    text(
        576, 104, "Epic → split into Tasks · vague → questions · never runs code", 12.5, t["muted"]
    )
    arrow(240, 74, 288, 74, both=True)
    arrow(508, 74, 556, 74)

    # --- subagents panel
    rect(32, 164, 640, 256, t["panel"], t["stroke"], 14)
    label(52, 190, "SUBAGENTS · EACH WITH ITS OWN TOOLS")
    subs = [
        ("researcher", "specs, ADRs, web, Jira"),
        ("code_analyst", "code, commits, open PRs"),
        ("developer", "edits code · runs tests"),
        ("reviewer", "structured verdict on diff"),
        ("github_publisher", "branch · push · PR"),
        ("jira_agent", "comment · move · create"),
    ]
    for i, (name, role) in enumerate(subs):
        cx, cy = 48 + (i % 3) * 208, 206 + (i // 3) * 104
        hl = name == "developer"
        card(
            cx,
            cy,
            192,
            88,
            name,
            role,
            fill=t["accent_bg"] if hl else None,
            stroke=t["accent"] if hl else None,
            tcolor=t["accent_text"] if hl else None,
        )
        if hl:
            text(cx + 16, cy + 76, "only agent that writes code", 11, t["accent_text"], 500)
    arrow(614, 116, 614, 164, "task", 622, 146)

    # --- guarded MCP layer
    rect(704, 164, 384, 256, t["panel"], t["stroke"], 14)
    label(724, 190, "GUARDED MCP LAYER · EVERY CALL")
    steps = [
        (
            "1",
            "Allowlist",
            "13 named tools, a subset per subagent",
            t["card"],
            t["stroke"],
            t["text"],
        ),
        (
            "2",
            "Human approval",
            "every write pauses: interrupt_on",
            t["warn_bg"],
            t["warn"],
            t["text"],
        ),
        (
            "3",
            "Authorization + audit",
            "repo · shipwright/* · Jira project",
            t["card"],
            t["stroke"],
            t["text"],
        ),
    ]
    for i, (n, title, sub, fill, stroke, tc) in enumerate(steps):
        sy = 202 + i * 54
        rect(720, sy, 352, 46, fill, stroke, 10)
        dot = t["warn"] if n == "2" else t["muted"]
        a(f'<circle cx="744" cy="{sy + 23}" r="11" fill="{dot}"/>')
        text(744, sy + 27.5, n, 12, t["bg"], 700, anchor="middle")
        text(764, sy + 20, title, 13.5, tc, 600)
        text(764, sy + 37, sub, 11.5, t["muted"])
    for i, (name, sub) in enumerate([("GitHub", "remote MCP"), ("Jira", "mcp-atlassian")]):
        gx = 720 + i * 180
        rect(gx, 368, 172, 38, t["card"], t["stroke"], 10)
        text(gx + 14, 392, name, 13.5, t["text"], 600)
        text(gx + 158, 392, sub, 11.5, t["muted"], anchor="end")
    arrow(672, 292, 704, 292)

    # --- backend panel
    rect(32, 456, 640, 152, t["panel"], t["stroke"], 14)
    label(52, 482, "COMPOSITEBACKEND · FILES THE AGENTS SEE")
    back = [
        ("/", ["sandbox: repo clone,", "tests (local or LangSmith)"]),
        ("/memories/", ["per user, persistent", "(MongoDB store)"]),
        ("/policies/  /skills/", ["POLICIES.md, SKILL.md", "read-only for everyone"]),
    ]
    for i, (name, sub) in enumerate(back):
        card(48 + i * 208, 498, 192, 94, name, sub)
    arrow(352, 420, 352, 456)

    # --- services panel
    rect(704, 456, 384, 152, t["panel"], t["stroke"], 14)
    label(724, 482, "SERVICES")
    svcs = [
        ("OpenAI", "models"),
        ("MongoDB", "state · audit"),
        ("Pinecone", "knowledge"),
        ("Tavily", "web search"),
        ("LangSmith", "traces · deploy"),
    ]
    for i, (name, sub) in enumerate(svcs):
        sx, sy = 720 + (i % 2) * 180, 498 + (i // 2) * 36
        rect(sx, sy, 172, 30, t["card"], t["stroke"], 8)
        text(sx + 12, sy + 20, name, 12.5, t["text"], 600)
        text(sx + 160, sy + 20, sub, 11, t["muted"], anchor="end")

    a("</svg>")
    return "\n".join(o)


OUT.mkdir(parents=True, exist_ok=True)
for name, theme in THEMES.items():
    (OUT / f"architecture-{name}.svg").write_text(build(theme), encoding="utf-8")
print("ok")
