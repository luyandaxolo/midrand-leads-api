"""Midrand leads API — shared lead capture + client dashboards for demo websites."""
import os, html, secrets
from datetime import datetime, timezone

import psycopg
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

DATABASE_URL = os.environ["DATABASE_URL"]
# CLIENTS env format: postkey:slug:Business Name:viewkey,postkey:slug:Name:viewkey,...
def parse_clients():
    clients = {}
    for chunk in os.environ.get("CLIENTS", "").split(","):
        parts = chunk.strip().split(":")
        if len(parts) == 4:
            postkey, slug, name, viewkey = parts
            clients[postkey] = {"slug": slug, "name": name, "viewkey": viewkey}
    return clients

CLIENTS = parse_clients()

app = FastAPI(title="Midrand Leads API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DDL = """
CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    client_slug TEXT NOT NULL,
    name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    service TEXT DEFAULT '',
    estimate TEXT DEFAULT '',
    message TEXT DEFAULT '',
    page TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    contacted BOOLEAN NOT NULL DEFAULT FALSE
);
"""

@app.on_event("startup")
def startup():
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(DDL)
        conn.commit()

def client_by_viewkey(key):
    for postkey, c in CLIENTS.items():
        if c["viewkey"] == key:
            return c
    return None

@app.get("/health")
def health():
    return {"ok": True, "clients": sorted(c["slug"] for c in CLIENTS.values())}

@app.post("/capture")
async def capture(req: Request):
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    postkey = str(body.get("clientKey", ""))
    client = CLIENTS.get(postkey)
    if not client:
        raise HTTPException(403, "unknown client key")
    name = str(body.get("name", ""))[:200]
    phone = str(body.get("phone", ""))[:50]
    service = str(body.get("service", ""))[:300]
    estimate = str(body.get("estimate", ""))[:100]
    message = str(body.get("message", ""))[:1000]
    page = str(body.get("page", ""))[:300]
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            "INSERT INTO leads (client_slug, name, phone, service, estimate, message, page) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (client["slug"], name, phone, service, estimate, message, page),
        )
        conn.commit()
    return {"ok": True}

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Leads — {name}</title>
<style>
body{{margin:0;background:#0d0d0c;color:#e8e4dc;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif}}
header{{padding:18px 20px;border-bottom:1px solid #262620;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}}
h1{{font-size:17px;margin:0;letter-spacing:.06em;text-transform:uppercase}}
.badge{{color:#c9803c;font-size:12px;letter-spacing:.08em}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;padding:12px 14px;border-bottom:1px solid #22221e;vertical-align:top}}
th{{color:#8f8a7d;font-size:11px;letter-spacing:.1em;text-transform:uppercase}}
tr.done td{{opacity:.45}}
a{{color:#c9803c}}
.empty{{padding:40px 20px;color:#8f8a7d}}
.btn{{display:inline-block;background:none;border:1px solid #c9803c;color:#c9803c;padding:5px 10px;font-size:12px;cursor:pointer;text-decoration:none;border-radius:2px}}
.wrap{{overflow-x:auto}}
</style></head><body>
<header><h1>Leads — {name}</h1><span class="badge">{count} lead{'s' if count != 1 else ''} · updated {now}</span></header>
<div class="wrap">{rows}</div>
</body></html>"""

ROW = """<tr class="{done}">
<td><strong>{when}</strong></td><td>{name}<br><a href="tel:{phone}">{phone}</a></td>
<td>{service}<br><span style="color:#8f8a7d">{estimate}</span></td>
<td>{message}</td><td>{action}</td></tr>"""

@app.get("/leads/{slug}", response_class=HTMLResponse)
def leads(slug: str, key: str = ""):
    client = client_by_viewkey(key)
    if not client or client["slug"] != slug:
        raise HTTPException(401, "invalid access key")
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT id, name, phone, service, estimate, message, page, created_at, contacted FROM leads WHERE client_slug=%s ORDER BY created_at DESC LIMIT 500",
            (slug,),
        ).fetchall()
    out = []
    for (lid, name, phone, service, estimate, message, page, created_at, contacted) in rows:
        when = created_at.astimezone().strftime("%d %b %Y, %H:%M")
        action = (f'<a class="btn" href="/leads/{slug}/done/{lid}?key={key}">Mark contacted</a>'
                  if not contacted else "Contacted ✓")
        out.append(ROW.format(
            done="done" if contacted else "",
            when=html.escape(when), name=html.escape(name) or "—",
            phone=html.escape(phone), service=html.escape(service), estimate=html.escape(estimate),
            message=html.escape(message), action=action))
    table = ("<table><tr><th>When</th><th>Who</th><th>What</th><th>Message</th><th></th></tr>" + "".join(out) + "</table>"
             if out else '<div class="empty">No leads yet. They will appear here the moment someone uses the quote tool or contact form on the website.</div>')
    return PAGE.format(name=html.escape(client["name"]), count=len(out),
                       now=datetime.now(timezone.utc).strftime("%d %b %H:%M UTC"), rows=table)

@app.get("/leads/{slug}/done/{lead_id}")
def mark_done(slug: str, lead_id: int, key: str = ""):
    client = client_by_viewkey(key)
    if not client or client["slug"] != slug:
        raise HTTPException(401, "invalid access key")
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("UPDATE leads SET contacted=TRUE WHERE id=%s AND client_slug=%s", (lead_id, slug))
        conn.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/leads/{slug}?key={key}")
