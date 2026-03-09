import sqlite3
import json
import io
import base64
import os
import hashlib
from datetime import datetime, date
from flask import Flask, request, jsonify, render_template, g

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "stock.db")

# ─── DATABASE ────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

# ─── QR TOKEN — généré UNE SEULE FOIS à la création ─────────────────────────

def make_qr_token(id_boite: str, created_at: str) -> str:
    """
    Token unique et PERMANENT basé sur id_boite + date de création.
    Ne change JAMAIS même si l'article est modifié.
    Détruit uniquement si la boîte est supprimée.
    """
    raw = f"{id_boite}::{created_at}::STOCK_MP_PERMANENT"
    return hashlib.sha256(raw.encode()).hexdigest()[:20].upper()

# ─── INIT DB ──────────────────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                id_boite        TEXT UNIQUE NOT NULL,
                ingredient      TEXT NOT NULL,
                categorie       TEXT,
                fournisseur     TEXT,
                lot             TEXT,
                date_reception  TEXT,
                date_peremption TEXT,
                quantite        TEXT,
                emplacement     TEXT,
                remarque        TEXT,
                qr_token        TEXT UNIQUE,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migration douce: ajoute qr_token si la colonne n'existe pas encore
        try:
            conn.execute("ALTER TABLE stock ADD COLUMN qr_token TEXT UNIQUE")
        except Exception:
            pass

        cur = conn.execute("SELECT COUNT(*) FROM stock")
        if cur.fetchone()[0] == 0:
            seed = [
                ("MP001","Sucre blanc","Sucrant","Cos","L250301","2026-03-01","2027-03-01","25 kg","Rack A1","Sac intact"),
                ("MP002","Lait en poudre","Produit laitier","AED","L250215","2026-02-28","2027-02-28","20 kg","Rack B2","Bien fermé"),
                ("MP003","Arôme fleur d'oranger","Arôme","BFE","L250210","2026-02-27","2027-02-27","5 L","Rack E1","Bouteille fermée"),
                ("MP004","Sucre glace","Sucrant","Cos","L250225","2026-03-03","2027-03-03","20 kg","Rack A2","Sac légèrement ouvert"),
            ]
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for s in seed:
                token = make_qr_token(s[0], created_at)
                conn.execute(
                    """INSERT INTO stock
                       (id_boite,ingredient,categorie,fournisseur,lot,
                        date_reception,date_peremption,quantite,emplacement,
                        remarque,qr_token,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*s, token, created_at)
                )
        else:
            # Génère les tokens manquants pour les anciennes lignes
            rows = conn.execute(
                "SELECT id_boite, created_at FROM stock WHERE qr_token IS NULL"
            ).fetchall()
            for row in rows:
                ca = row[1] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                token = make_qr_token(row[0], ca)
                conn.execute("UPDATE stock SET qr_token=? WHERE id_boite=?", (token, row[0]))
        conn.commit()

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def row_to_dict(row):
    return dict(row)

def next_id(conn):
    cur = conn.execute("SELECT id_boite FROM stock ORDER BY id_boite")
    ids = [r[0] for r in cur.fetchall() if r[0].startswith("MP")]
    nums = []
    for i in ids:
        try: nums.append(int(i[2:]))
        except: pass
    return f"MP{(max(nums)+1 if nums else 1):03d}"

# ─── API ROUTES ───────────────────────────────────────────────────────────────

@app.route("/api/stock", methods=["GET"])
def get_stock():
    db = get_db()
    search    = request.args.get("search", "").lower()
    categorie = request.args.get("categorie", "")
    sort      = request.args.get("sort", "id_boite")
    order     = request.args.get("order", "asc")

    allowed_sort = {"id_boite","ingredient","categorie","fournisseur","lot",
                    "date_reception","date_peremption","quantite","emplacement"}
    if sort not in allowed_sort:
        sort = "id_boite"
    direction = "ASC" if order == "asc" else "DESC"

    query  = "SELECT * FROM stock WHERE 1=1"
    params = []

    if search:
        query += " AND (LOWER(ingredient) LIKE ? OR LOWER(fournisseur) LIKE ? OR LOWER(lot) LIKE ? OR LOWER(id_boite) LIKE ? OR LOWER(emplacement) LIKE ?)"
        params += [f"%{search}%"] * 5

    if categorie:
        query += " AND categorie = ?"
        params.append(categorie)

    query += f" ORDER BY {sort} {direction}"
    rows = db.execute(query, params).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/stock/categories", methods=["GET"])
def get_categories():
    db   = get_db()
    rows = db.execute(
        "SELECT DISTINCT categorie FROM stock WHERE categorie IS NOT NULL AND categorie != '' ORDER BY categorie"
    ).fetchall()
    return jsonify([r[0] for r in rows])


@app.route("/api/stock/stats", methods=["GET"])
def get_stats():
    db    = get_db()
    total = db.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    warn  = db.execute(
        "SELECT COUNT(*) FROM stock WHERE date_peremption IS NOT NULL AND date_peremption != '' AND date(date_peremption) <= date(?, '+90 days')",
        (today,)
    ).fetchone()[0]
    cats  = db.execute(
        "SELECT COUNT(DISTINCT categorie) FROM stock WHERE categorie IS NOT NULL AND categorie != ''"
    ).fetchone()[0]
    return jsonify({"total": total, "warn": warn, "categories": cats})


@app.route("/api/stock/next-id", methods=["GET"])
def api_next_id():
    db = get_db()
    return jsonify({"next_id": next_id(db)})


@app.route("/api/stock", methods=["POST"])
def add_item():
    db   = get_db()
    data = request.get_json()
    if not data.get("id_boite") or not data.get("ingredient"):
        return jsonify({"error": "id_boite et ingredient sont obligatoires"}), 400

    existing = db.execute("SELECT id FROM stock WHERE id_boite = ?", (data["id_boite"],)).fetchone()
    if existing:
        return jsonify({"error": f"L'ID {data['id_boite']} existe déjà"}), 409

    # ✅ QR token généré ici une seule fois, jamais modifié après
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    qr_token   = make_qr_token(data["id_boite"], created_at)

    db.execute("""
        INSERT INTO stock
            (id_boite,ingredient,categorie,fournisseur,lot,
             date_reception,date_peremption,quantite,emplacement,
             remarque,qr_token,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("id_boite"), data.get("ingredient"), data.get("categorie",""),
        data.get("fournisseur",""), data.get("lot",""), data.get("date_reception",""),
        data.get("date_peremption",""), data.get("quantite",""),
        data.get("emplacement",""), data.get("remarque",""),
        qr_token, created_at
    ))
    db.commit()
    row = db.execute("SELECT * FROM stock WHERE id_boite = ?", (data["id_boite"],)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/stock/<id_boite>", methods=["GET"])
def get_item(id_boite):
    db  = get_db()
    row = db.execute("SELECT * FROM stock WHERE id_boite = ?", (id_boite,)).fetchone()
    if not row:
        return jsonify({"error": "Article non trouvé"}), 404
    return jsonify(row_to_dict(row))


@app.route("/api/stock/<id_boite>", methods=["PUT"])
def update_item(id_boite):
    db   = get_db()
    data = request.get_json()
    existing = db.execute("SELECT id FROM stock WHERE id_boite = ?", (id_boite,)).fetchone()
    if not existing:
        return jsonify({"error": "Article non trouvé"}), 404

    # ⚠️ qr_token et created_at ne sont PAS modifiés ici — jamais
    db.execute("""
        UPDATE stock SET
            ingredient=?, categorie=?, fournisseur=?, lot=?,
            date_reception=?, date_peremption=?, quantite=?,
            emplacement=?, remarque=?, updated_at=datetime('now')
        WHERE id_boite=?
    """, (
        data.get("ingredient"), data.get("categorie",""),
        data.get("fournisseur",""), data.get("lot",""),
        data.get("date_reception",""), data.get("date_peremption",""),
        data.get("quantite",""), data.get("emplacement",""),
        data.get("remarque",""), id_boite
    ))
    db.commit()
    row = db.execute("SELECT * FROM stock WHERE id_boite = ?", (id_boite,)).fetchone()
    return jsonify(row_to_dict(row))


@app.route("/api/stock/<id_boite>", methods=["DELETE"])
def delete_item(id_boite):
    db = get_db()
    existing = db.execute("SELECT id FROM stock WHERE id_boite = ?", (id_boite,)).fetchone()
    if not existing:
        return jsonify({"error": "Article non trouvé"}), 404
    # Suppression → qr_token détruit avec la ligne
    db.execute("DELETE FROM stock WHERE id_boite = ?", (id_boite,))
    db.commit()
    return jsonify({"message": f"{id_boite} supprimé avec succès"})


@app.route("/api/stock/<id_boite>/qr-data", methods=["GET"])
def get_qr_data(id_boite):
    db  = get_db()
    row = db.execute("SELECT * FROM stock WHERE id_boite = ?", (id_boite,)).fetchone()
    if not row:
        return jsonify({"error": "Article non trouvé"}), 404
    item = row_to_dict(row)
    # Le QR encode uniquement l'URL de scan → pointe vers les données actuelles
    base_url = request.host_url.rstrip("/")
    qr_text  = f"{base_url}/scan/{item['qr_token']}"
    return jsonify({"qr_text": qr_text, "item": item})


# ─── PAGE DE SCAN ─────────────────────────────────────────────────────────────

@app.route("/scan/<token>", methods=["GET"])
def scan_qr(token):
    """
    Page accessible en scannant le QR code avec un smartphone.
    Affiche toujours les données ACTUELLES de la boîte.
    Le QR ne change jamais car le token est permanent.
    """
    db  = get_db()
    row = db.execute("SELECT * FROM stock WHERE qr_token = ?", (token,)).fetchone()
    if not row:
        return """<html><body style='font-family:sans-serif;text-align:center;padding:60px;background:#0b0d12;color:#f05e5e'>
        <h2>❌ QR Code invalide ou boîte supprimée.</h2></body></html>""", 404

    item  = row_to_dict(row)
    today = date.today()
    days  = None
    if item.get("date_peremption"):
        try:
            d    = date.fromisoformat(item["date_peremption"])
            days = (d - today).days
        except Exception:
            pass

    if days is None:
        sc, st = "#3ecfa3", "✅ Stock OK"
    elif days < 0:
        sc, st = "#f05e5e", "❌ Expiré"
    elif days < 90:
        sc, st = "#f5a623", f"⚠️ Expire dans {days} jours"
    else:
        sc, st = "#3ecfa3", "✅ Stock OK"

    def f(k): return item.get(k) or "—"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{f('ingredient')} — Stock MP</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=JetBrains+Mono:wght@500&display=swap');
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Syne',sans-serif;background:#0b0d12;color:#dde2f0;min-height:100vh;padding:20px 14px}}
  .card{{max-width:460px;margin:0 auto;background:#111520;border:1px solid #252d42;border-radius:16px;overflow:hidden}}
  .hdr{{background:#1f2435;padding:22px;text-align:center;border-bottom:1px solid #252d42}}
  .id{{font-family:'JetBrains Mono',monospace;font-size:11px;background:#0b0d12;border:1px solid #252d42;border-radius:5px;padding:3px 10px;color:#f0c93a;display:inline-block;margin-bottom:8px}}
  .name{{font-size:21px;font-weight:800}}
  .pill{{display:inline-block;margin-top:10px;padding:4px 14px;border-radius:20px;font-size:11px;font-weight:700;background:{sc}22;color:{sc};border:1px solid {sc}}}
  .upd{{font-size:10px;color:#5a6380;margin-top:6px}}
  .grid{{padding:16px;display:grid;grid-template-columns:1fr 1fr;gap:8px}}
  .cell{{background:#1f2435;border-radius:8px;padding:9px 11px}}
  .cell-l{{font-size:9px;text-transform:uppercase;letter-spacing:.8px;color:#5a6380;margin-bottom:3px}}
  .cell-v{{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:500}}
  .full{{grid-column:1/-1}}
  .ftr{{text-align:center;padding:12px;border-top:1px solid #252d42;font-size:10px;color:#5a6380}}
</style>
</head>
<body>
<div class="card">
  <div class="hdr">
    <div class="id">{f('id_boite')}</div>
    <div class="name">{f('ingredient')}</div>
    <div class="pill">{st}</div>
    <div class="upd">Dernière modification : {f('updated_at')[:10]}</div>
  </div>
  <div class="grid">
    <div class="cell"><div class="cell-l">Catégorie</div><div class="cell-v">{f('categorie')}</div></div>
    <div class="cell"><div class="cell-l">Fournisseur</div><div class="cell-v">{f('fournisseur')}</div></div>
    <div class="cell"><div class="cell-l">Lot</div><div class="cell-v">{f('lot')}</div></div>
    <div class="cell"><div class="cell-l">Quantité</div><div class="cell-v">{f('quantite')}</div></div>
    <div class="cell"><div class="cell-l">Réception</div><div class="cell-v">{f('date_reception')}</div></div>
    <div class="cell"><div class="cell-l">Péremption</div><div class="cell-v" style="color:{sc}">{f('date_peremption')}</div></div>
    <div class="cell full"><div class="cell-l">Emplacement</div><div class="cell-v">{f('emplacement')}</div></div>
    <div class="cell full"><div class="cell-l">Remarque</div><div class="cell-v">{f('remarque')}</div></div>
  </div>
  <div class="ftr">🏭 Gestion Stock — Matières Premières</div>
</div>
</body></html>"""
    return html


@app.route("/")
def index():
    return render_template("index.html")


# Init DB au démarrage
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n✅ Stock MP - Backend Flask + SQLite")
    print(f"🌐 Ouvrir: http://localhost:{port}\n")
    app.run(debug=False, host="0.0.0.0", port=port)
