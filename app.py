#!/usr/bin/env python3
"""
app.py — Portail de facturation VPN (AmneziaWG / WireGuard)
Lancer : python3 app.py
"""

import sqlite3
import hashlib
import subprocess
from datetime import date, datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, g)

app = Flask(__name__)
app.secret_key = "CHANGE_CE_SECRET_EN_PROD_SVP"

DB_PATH       = "/opt/vpn-billing/vpn_billing.db"
CONTAINER     = "amnezia-awg"
WG_INTERFACE  = "wg0"

# ─── Coordonnées de paiement affichées aux users ─────────────────────────────
BANK_INFO = {
    "beneficiaire": "Чеганг Анжес Уилфрид",
    "telephone":    "+7 996 637-23-58",
    "banque":       "Тбанк",
    "montant":      "100 ₽",
    "reference":    "VPN + твоё имя",
}

# ─── DB helpers ──────────────────────────────────────────────────────────────
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

def hash_password(p):
    return hashlib.sha256(p.encode()).hexdigest()

# ─── Auth helpers ─────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Accès réservé à l'administrateur.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated

# ─── iptables helpers ─────────────────────────────────────────────────────────
def iptables_block_peer(ip_vpn):
    """Bloque le trafic d'un peer par son IP VPN (iptables DROP).
    La config WireGuard reste intacte : le peer peut être réactivé sans reconfiguration."""
    ip = ip_vpn.split("/")[0]
    try:
        subprocess.run(
            ["docker", "exec", CONTAINER, "iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"],
            check=True, capture_output=True
        )
        subprocess.run(
            ["docker", "exec", CONTAINER, "iptables", "-I", "FORWARD", "-d", ip, "-j", "DROP"],
            check=True, capture_output=True
        )
        return True
    except subprocess.CalledProcessError as e:
        app.logger.error(f"iptables_block_peer failed: {e.stderr}")
        return False

def iptables_unblock_peer(ip_vpn):
    """Réactive un peer en supprimant les règles DROP (ignoré si déjà absent)."""
    ip = ip_vpn.split("/")[0]
    ok = True
    for direction in ["-s", "-d"]:
        try:
            subprocess.run(
                ["docker", "exec", CONTAINER, "iptables", "-D", "FORWARD", direction, ip, "-j", "DROP"],
                check=True, capture_output=True
            )
        except subprocess.CalledProcessError:
            pass  # règle absente = peer déjà débloqué, pas d'erreur
    return ok

# ─── Routes publiques ─────────────────────────────────────────────────────────
@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        nom   = request.form["nom"].strip()
        email = request.form["email"].strip().lower()
        mdp   = request.form["password"]
        confirm = request.form["confirm"]
        if mdp != confirm:
            flash("Les mots de passe ne correspondent pas.", "danger")
            return render_template("inscription.html")
        if len(mdp) < 6:
            flash("Le mot de passe doit faire au moins 6 caractères.", "danger")
            return render_template("inscription.html")
        db = get_db()
        if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            flash("Cet email est déjà utilisé.", "danger")
            return render_template("inscription.html")
        db.execute(
            "INSERT INTO users (nom, email, password_hash, is_admin) VALUES (?, ?, ?, 0)",
            (nom, email, hash_password(mdp))
        )
        db.commit()
        new_user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        db.execute(
            "INSERT INTO abonnements (user_id, montant, statut) VALUES (?, 100, 'en_attente')",
            (new_user["id"],)
        )
        db.commit()
        flash("✅ Demande envoyée ! L'administrateur activera votre accès après réception du paiement.", "success")
        return redirect(url_for("login"))
    return render_template("inscription.html")


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        mdp   = request.form["password"]
        db    = get_db()
        user  = db.execute(
            "SELECT * FROM users WHERE email = ? AND password_hash = ?",
            (email, hash_password(mdp))
        ).fetchone()
        if user:
            session["user_id"]  = user["id"]
            session["user_nom"] = user["nom"]
            session["is_admin"] = bool(user["is_admin"])
            return redirect(url_for("admin_panel") if user["is_admin"] else url_for("dashboard"))
        flash("Email ou mot de passe incorrect.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── Portail utilisateur ──────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    db   = get_db()
    uid  = session["user_id"]
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    abo  = db.execute("SELECT * FROM abonnements WHERE user_id = ?", (uid,)).fetchone()
    peers = db.execute("SELECT * FROM peers WHERE user_id = ?", (uid,)).fetchall()
    paiements = db.execute(
        "SELECT * FROM paiements WHERE user_id = ? ORDER BY date_paiement DESC LIMIT 5",
        (uid,)
    ).fetchall()

    # Calcul statut abonnement
    statut_color = "success"
    jours_restants = None
    if abo and abo["date_fin"]:
        df = date.fromisoformat(abo["date_fin"])
        jours_restants = (df - date.today()).days
        if jours_restants < 0:
            statut_color = "danger"
        elif jours_restants <= 7:
            statut_color = "warning"

    return render_template("dashboard.html",
        user=user, abo=abo, peers=peers,
        paiements=paiements, bank=BANK_INFO,
        statut_color=statut_color, jours_restants=jours_restants,
        today=date.today()
    )

@app.route("/changer_mdp", methods=["POST"])
@login_required
def changer_mdp():
    uid      = session["user_id"]
    ancien   = request.form["ancien"]
    nouveau  = request.form["nouveau"]
    confirm  = request.form["confirm"]
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user["password_hash"] != hash_password(ancien):
        flash("Ancien mot de passe incorrect.", "danger")
    elif nouveau != confirm:
        flash("Les nouveaux mots de passe ne correspondent pas.", "danger")
    elif len(nouveau) < 6:
        flash("Le mot de passe doit faire au moins 6 caractères.", "danger")
    else:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (hash_password(nouveau), uid))
        db.commit()
        flash("Mot de passe mis à jour avec succès.", "success")
    return redirect(url_for("dashboard"))

# ─── Panel Admin ──────────────────────────────────────────────────────────────
@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    db = get_db()
    users = db.execute("""
        SELECT u.id, u.nom, u.email,
               a.date_debut, a.date_fin, a.montant, a.statut,
               COUNT(p.id) as nb_peers,
               SUM(CASE WHEN p.actif=1 THEN 1 ELSE 0 END) as peers_actifs
        FROM users u
        LEFT JOIN abonnements a ON a.user_id = u.id
        LEFT JOIN peers p ON p.user_id = u.id
        WHERE u.is_admin = 0
        GROUP BY u.id
        ORDER BY u.nom
    """).fetchall()

    paiements_en_attente = db.execute("""
        SELECT p.*, u.nom
        FROM paiements p
        JOIN users u ON u.id = p.user_id
        WHERE p.valide = 0
        ORDER BY p.date_paiement DESC
    """).fetchall()

    demandes = db.execute("""
        SELECT u.id, u.nom, u.email, u.created_at
        FROM users u
        JOIN abonnements a ON a.user_id = u.id
        WHERE u.is_admin = 0 AND a.statut = 'en_attente'
        ORDER BY u.created_at DESC
    """).fetchall()

    return render_template("admin.html",
        users=users,
        paiements_en_attente=paiements_en_attente,
        demandes=demandes,
        today=date.today()
    )

@app.route("/admin/user/<int:uid>")
@login_required
@admin_required
def admin_user_detail(uid):
    db = get_db()
    user  = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    abo   = db.execute("SELECT * FROM abonnements WHERE user_id = ?", (uid,)).fetchone()
    peers = db.execute("SELECT * FROM peers WHERE user_id = ?", (uid,)).fetchall()
    histo = db.execute(
        "SELECT * FROM paiements WHERE user_id = ? ORDER BY date_paiement DESC",
        (uid,)
    ).fetchall()
    return render_template("admin_user.html",
        user=user, abo=abo, peers=peers, histo=histo, today=date.today()
    )

@app.route("/admin/paiement/ajouter", methods=["POST"])
@login_required
@admin_required
def admin_ajouter_paiement():
    uid     = int(request.form["user_id"])
    montant = float(request.form["montant"])
    mois    = int(request.form["mois"])
    note    = request.form.get("note", "")
    db = get_db()

    # Enregistre le paiement
    db.execute("""
        INSERT INTO paiements (user_id, montant, mois_prolonges, note, valide)
        VALUES (?, ?, ?, ?, 1)
    """, (uid, montant, mois, note))

    # Prolonge l'abonnement
    abo = db.execute("SELECT * FROM abonnements WHERE user_id = ?", (uid,)).fetchone()
    if abo and abo["date_fin"]:
        base = max(date.fromisoformat(abo["date_fin"]), date.today())
    else:
        base = date.today()
    nouvelle_fin = base + timedelta(days=30 * mois)

    db.execute("""
        UPDATE abonnements
        SET date_fin = ?, date_debut = COALESCE(date_debut, ?), statut = 'actif'
        WHERE user_id = ?
    """, (nouvelle_fin.isoformat(), date.today().isoformat(), uid))

    # Réactive tous les peers de cet user
    peers = db.execute("SELECT * FROM peers WHERE user_id = ?", (uid,)).fetchall()
    for peer in peers:
        if not peer["actif"]:
            iptables_unblock_peer(peer["ip_vpn"])
            db.execute("UPDATE peers SET actif = 1 WHERE id = ?", (peer["id"],))

    db.commit()
    user = db.execute("SELECT nom FROM users WHERE id = ?", (uid,)).fetchone()
    flash(f"✅ Paiement enregistré pour {user['nom']} — abonnement jusqu'au {nouvelle_fin.strftime('%d/%m/%Y')}.", "success")
    return redirect(url_for("admin_user_detail", uid=uid))

@app.route("/admin/peer/suspendre/<int:peer_id>", methods=["POST"])
@login_required
@admin_required
def admin_suspendre_peer(peer_id):
    db   = get_db()
    peer = db.execute("SELECT * FROM peers WHERE id = ?", (peer_id,)).fetchone()
    if peer:
        ok = iptables_block_peer(peer["ip_vpn"])
        db.execute("UPDATE peers SET actif = 0 WHERE id = ?", (peer_id,))
        db.commit()
        flash(f"Peer {peer['label']} ({peer['ip_vpn']}) suspendu {'✅' if ok else '⚠ (erreur iptables)'}.", "warning")
    return redirect(request.referrer or url_for("admin_panel"))

@app.route("/admin/peer/reactiver/<int:peer_id>", methods=["POST"])
@login_required
@admin_required
def admin_reactiver_peer(peer_id):
    db   = get_db()
    peer = db.execute("SELECT * FROM peers WHERE id = ?", (peer_id,)).fetchone()
    if peer:
        ok = iptables_unblock_peer(peer["ip_vpn"])
        db.execute("UPDATE peers SET actif = 1 WHERE id = ?", (peer_id,))
        db.commit()
        flash(f"Peer {peer['label']} ({peer['ip_vpn']}) réactivé {'✅' if ok else '⚠ (erreur iptables)'}.", "success")
    return redirect(request.referrer or url_for("admin_panel"))

@app.route("/admin/user/suspendre_tout/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_suspendre_tout(uid):
    db    = get_db()
    peers = db.execute("SELECT * FROM peers WHERE user_id = ? AND actif = 1", (uid,)).fetchall()
    for peer in peers:
        iptables_block_peer(peer["ip_vpn"])
        db.execute("UPDATE peers SET actif = 0 WHERE id = ?", (peer["id"],))
    db.execute("UPDATE abonnements SET statut = 'suspendu' WHERE user_id = ?", (uid,))
    db.commit()
    user = db.execute("SELECT nom FROM users WHERE id = ?", (uid,)).fetchone()
    flash(f"Tous les accès de {user['nom']} ont été suspendus.", "warning")
    return redirect(url_for("admin_user_detail", uid=uid))

@app.route("/admin/user/creer", methods=["POST"])
@login_required
@admin_required
def admin_creer_user():
    nom    = request.form["nom"].strip()
    email  = request.form["email"].strip().lower()
    mdp    = request.form["password"]
    db = get_db()
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        flash(f"L'email {email} est déjà utilisé.", "danger")
        return redirect(url_for("admin_panel"))
    db.execute(
        "INSERT INTO users (nom, email, password_hash, is_admin) VALUES (?, ?, ?, 0)",
        (nom, email, hash_password(mdp))
    )
    db.commit()
    new_user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    db.execute(
        "INSERT INTO abonnements (user_id, montant, statut) VALUES (?, 100, 'expire')",
        (new_user["id"],)
    )
    db.commit()
    flash(f"✅ Utilisateur {nom} créé avec succès.", "success")
    return redirect(url_for("admin_user_detail", uid=new_user["id"]))


@app.route("/admin/abonnement/modifier", methods=["POST"])
@login_required
@admin_required
def admin_modifier_abo():
    uid      = int(request.form["user_id"])
    date_fin = request.form["date_fin"]
    montant  = float(request.form["montant"])
    db = get_db()
    db.execute("""
        UPDATE abonnements SET date_fin = ?, montant = ?,
               date_debut = COALESCE(date_debut, ?), statut = 'actif'
        WHERE user_id = ?
    """, (date_fin, montant, date.today().isoformat(), uid))
    db.commit()
    flash("Abonnement mis à jour.", "success")
    return redirect(url_for("admin_user_detail", uid=uid))

# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
