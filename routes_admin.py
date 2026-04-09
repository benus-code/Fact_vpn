#!/usr/bin/env python3
"""
routes_admin.py — Blueprint admin (nouveau dashboard sombre)
Toutes les routes /admin/* du nouveau dashboard sont ici.
Les anciennes routes admin dans app.py restent actives pendant la transition.
"""

import sqlite3
from datetime import datetime, date, timedelta
from calendar import monthrange
from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash, g, jsonify, current_app)
from functools import wraps
from vpn_utils import (
    get_all_peers_status, get_peers_status, container_is_running,
    format_bytes, format_age, peer_status_label, get_kpis,
    apply_keepalive, log_admin_action, CONTAINERS, get_keepalive_alerts,
)
from vpn_access import get_current_allowed_ips, BLACKHOLE_IP

admin_bp = Blueprint('admin_bp', __name__, url_prefix='/admin/v2')

DB_PATH = "/opt/vpn-billing/vpn_billing.db"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Accès réservé à l\'administrateur.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def _unread_alerts():
    db = get_db()
    try:
        row = db.execute("SELECT COUNT(*) FROM alertes WHERE lu = 0").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def _container_summaries():
    """Résumé running/total/connected pour chaque container — injecté dans base."""
    all_live = get_all_peers_status()
    now_ts = int(datetime.utcnow().timestamp())
    result = []
    db = get_db()

    for cname, cfg in CONTAINERS.items():
        running = container_is_running(cname)
        live = get_peers_status(cname) if running else {}
        total = db.execute(
            "SELECT COUNT(*) FROM peers WHERE container = ? AND actif = 1",
            (cname,)
        ).fetchone()[0]
        connected = sum(
            1 for d in live.values()
            if d.get('handshake_ts') and (now_ts - d['handshake_ts']) < 180
        )
        result.append({
            'container': cname,
            'label':     cfg['label'],
            'interface': cfg['interface'],
            'port':      cfg['port'],
            'running':   running,
            'total':     total,
            'connected': connected,
        })
    return result


# Injecteur de contexte global pour toutes les vues du blueprint
@admin_bp.context_processor
def inject_admin_globals():
    return {
        'unread_alerts': _unread_alerts(),
        'containers':    _container_summaries(),
    }


# ─── Helpers enrichissement peers ─────────────────────────────────────────────

def _enrich_peers(peers, all_live=None):
    """
    Enrichit une liste de rows peers avec les données live + formatées.
    Retourne une liste de dicts.
    """
    if all_live is None:
        all_live = get_all_peers_status()
    now_ts = int(datetime.utcnow().timestamp())
    result = []

    for p in peers:
        bare_ip = (p['ip_vpn'] or '').split('/')[0]
        live    = all_live.get(bare_ip, {})
        hs_ts   = live.get('handshake_ts', 0)
        hs_age  = live.get('handshake_age_s')
        ka      = live.get('keepalive', 0)

        u_is_banned = bool(p.get('is_banned', 0))
        abo_statut  = p.get('abo_statut')
        date_fin    = p.get('date_fin')
        today_iso   = date.today().isoformat()

        if not p['actif'] or u_is_banned:
            status_info = {'label': 'Suspendu', 'dot': 'red',   'badge': 'danger'}
        elif abo_statut in ('expire', 'suspendu', None) or not date_fin or date_fin < today_iso:
            status_info = {'label': 'Expiré',   'dot': 'amber', 'badge': 'warning'}
        else:
            status_info = peer_status_label(hs_ts)

        warn_ka = (p['actif'] and ka != 25 and hs_ts and (now_ts - hs_ts) < 600)

        if status_info['dot'] == 'green':
            filter_key = 'connecte'
        elif not p['actif'] or u_is_banned:
            filter_key = 'suspendu'
        else:
            filter_key = 'inactif'

        item = dict(p)
        item.update({
            'status':       status_info['dot'],
            'status_label': status_info['label'],
            'filter_key':   filter_key,
            'hs_label':     format_age(hs_age),
            'transfer_rx':  format_bytes(live.get('rx')),
            'transfer_tx':  format_bytes(live.get('tx')),
            'keepalive':    ka,
            'warn_ka':      warn_ka,
        })

        result.append(item)
    return result


# ─── Vue d'ensemble ───────────────────────────────────────────────────────────

@admin_bp.route('/')
@admin_bp.route('/overview')
@admin_required
def overview():
    db   = get_db()
    kpis = get_kpis(DB_PATH)

    # Expirations prochaines 7 jours
    expiring = db.execute("""
        SELECT u.id, u.nom, a.date_fin,
               CAST(julianday(a.date_fin) - julianday('now') AS INTEGER) AS jours
        FROM abonnements a
        JOIN users u ON u.id = a.user_id
        WHERE a.statut = 'actif'
          AND a.date_fin BETWEEN date('now') AND date('now', '+7 days')
        ORDER BY a.date_fin ASC
    """).fetchall()

    # Activité récente (paiements + inscriptions)
    activite_pay = db.execute("""
        SELECT p.date_paiement AS date, 'paiement' AS type,
               u.id AS user_id, u.nom,
               (p.montant || ' ₽ — +' || p.mois_prolonges || ' mois') AS detail
        FROM paiements p
        JOIN users u ON u.id = p.user_id
        ORDER BY p.date_paiement DESC
        LIMIT 15
    """).fetchall()

    activite_ins = db.execute("""
        SELECT created_at AS date, 'inscription' AS type,
               id AS user_id, nom, email AS detail
        FROM users
        WHERE is_admin = 0
        ORDER BY created_at DESC
        LIMIT 5
    """).fetchall()

    activite = sorted(
        [dict(r) for r in activite_pay] + [dict(r) for r in activite_ins],
        key=lambda x: x['date'],
        reverse=True
    )[:20]

    # Demandes en attente: clients sans abonnement ou en_attente
    pending_users = db.execute("""
        SELECT u.id, u.nom, u.email, u.whatsapp, u.telegram,
               u.created_at,
               COALESCE(a.statut, 'sans_abo') as abo_statut
        FROM users u
        LEFT JOIN abonnements a ON a.user_id = u.id
        WHERE u.is_admin = 0 AND u.is_banned = 0
          AND (a.id IS NULL OR a.statut IN ('en_attente', 'suspendu'))
        ORDER BY u.created_at DESC
        LIMIT 20
    """).fetchall()

    # IPs orphelines: peers live sans entrée DB
    all_live = get_all_peers_status()
    db_ips   = set(
        r[0].split('/')[0]
        for r in db.execute("SELECT ip_vpn FROM peers WHERE actif = 1").fetchall()
        if r[0]
    )
    orphan_ips = [
        {'ip': ip, **data}
        for ip, data in all_live.items()
        if ip not in db_ips
    ]

    return render_template('admin/overview.html',
                           kpis=kpis,
                           expiring=expiring,
                           activite=activite,
                           pending_users=pending_users,
                           orphan_ips=orphan_ips)


# ─── Clients ──────────────────────────────────────────────────────────────────

@admin_bp.route('/clients')
@admin_required
def clients():
    db = get_db()
    rows = db.execute("""
        SELECT u.id, u.nom, u.email, u.whatsapp, u.telegram,
               u.is_banned, u.referred_by,
               a.statut, a.date_fin, a.montant,
               CAST(julianday(a.date_fin) - julianday('now') AS INTEGER) AS jours_restants,
               (SELECT COUNT(*) FROM peers WHERE user_id = u.id) AS nb_peers,
               (SELECT COUNT(*) FROM peers WHERE user_id = u.id AND actif = 1) AS peers_actifs
        FROM users u
        LEFT JOIN abonnements a ON a.user_id = u.id
        WHERE u.is_admin = 0
        ORDER BY u.id DESC
    """).fetchall()

    users = []
    for r in rows:
        d = dict(r)
        # Déterminer filtre
        if r['is_banned']:
            d['statut'] = 'banni'
        elif r['statut'] is None:
            d['statut'] = 'en_attente'
        elif r['statut'] == 'actif' and r['date_fin']:
            today = date.today().isoformat()
            if r['date_fin'] < today:
                d['statut'] = 'expire'
        users.append(d)

    return render_template('admin/clients.html', users=users)


# ─── Détail client ────────────────────────────────────────────────────────────

@admin_bp.route('/clients/<int:uid>')
@admin_required
def client_detail(uid):
    db = get_db()

    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not user:
        flash('Client introuvable.', 'danger')
        return redirect(url_for('admin_bp.clients'))

    abo = db.execute(
        "SELECT * FROM abonnements WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (uid,)
    ).fetchone()

    peers_rows = db.execute("""
        SELECT p.*, u.is_banned,
               a.statut AS abo_statut, a.date_fin
        FROM peers p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN abonnements a ON a.user_id = p.user_id
            AND a.date_fin = (SELECT MAX(date_fin) FROM abonnements WHERE user_id = p.user_id)
        WHERE p.user_id = ?
        ORDER BY p.id DESC
    """, (uid,)).fetchall()

    histo = db.execute(
        "SELECT * FROM paiements WHERE user_id = ? ORDER BY date_paiement DESC",
        (uid,)
    ).fetchall()

    # Parrain
    parrain = None
    if user['referred_by']:
        parrain = db.execute(
            "SELECT id, nom FROM users WHERE referral_code = ?",
            (user['referred_by'],)
        ).fetchone()

    # Peers live
    all_live  = get_all_peers_status()
    peers     = _enrich_peers(peers_rows, all_live)
    peers_live = {
        p['ip_vpn'].split('/')[0]: all_live.get(p['ip_vpn'].split('/')[0], {})
        for p in peers_rows
    }

    return render_template('admin/client_detail.html',
                           user=user,
                           abo=abo,
                           peers=peers,
                           peers_live=peers_live,
                           histo=histo,
                           parrain=parrain,
                           peer_status=peer_status_label,
                           format_age=format_age,
                           format_bytes=format_bytes)


# ─── Peers ────────────────────────────────────────────────────────────────────

@admin_bp.route('/peers')
@admin_required
def peers():
    db = get_db()
    rows = db.execute("""
        SELECT p.*, u.nom AS user_nom, u.is_banned,
               a.statut AS abo_statut, a.date_fin
        FROM peers p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN abonnements a ON a.user_id = p.user_id
            AND a.date_fin = (SELECT MAX(date_fin) FROM abonnements WHERE user_id = p.user_id)
        ORDER BY p.id DESC
    """).fetchall()

    all_live  = get_all_peers_status()
    now_ts    = int(datetime.utcnow().timestamp())
    connected = sum(
        1 for d in all_live.values()
        if d.get('handshake_ts') and (now_ts - d['handshake_ts']) < 180
    )

    peers = _enrich_peers(rows, all_live)
    return render_template('admin/peers.html',
                           peers=peers,
                           connected=connected)


# ─── Abonnements ──────────────────────────────────────────────────────────────

@admin_bp.route('/abonnements')
@admin_required
def abonnements():
    db   = get_db()
    rows = db.execute("""
        SELECT a.id, a.user_id, a.statut, a.date_debut, a.date_fin,
               a.montant, a.reminded_j3, a.reminded_j1,
               u.nom,
               CAST(julianday(a.date_fin) - julianday('now') AS INTEGER) AS jours
        FROM abonnements a
        JOIN users u ON u.id = a.user_id
        ORDER BY a.id DESC
    """).fetchall()

    today = date.today().isoformat()
    in7   = (date.today() + timedelta(days=7)).isoformat()

    abos = []
    for r in rows:
        d = dict(r)
        # filter_key pour les pills
        if r['statut'] == 'suspendu':
            d['filter_key'] = 'suspendu'
        elif r['statut'] == 'actif' and r['date_fin'] and r['date_fin'] < today:
            d['filter_key'] = 'expire'
            d['statut']     = 'expire'
        elif r['statut'] == 'actif' and r['date_fin'] and r['date_fin'] <= in7:
            d['filter_key'] = 'soon'
        elif r['statut'] == 'actif':
            d['filter_key'] = 'actif'
        else:
            d['filter_key'] = r['statut'] or 'expire'
        abos.append(d)

    return render_template('admin/abonnements.html', abos=abos)


# ─── Paiements ────────────────────────────────────────────────────────────────

@admin_bp.route('/paiements')
@admin_required
def paiements():
    db   = get_db()
    rows = db.execute("""
        SELECT p.id, p.user_id, p.date_paiement, p.montant,
               p.mois_prolonges, p.note,
               u.nom
        FROM paiements p
        JOIN users u ON u.id = p.user_id
        ORDER BY p.date_paiement DESC
    """).fetchall()

    now        = datetime.utcnow()
    first_day  = now.replace(day=1).date().isoformat()
    last_month = (now.replace(day=1) - timedelta(days=1))
    lm_first   = last_month.replace(day=1).date().isoformat()
    lm_last    = last_month.date().isoformat()

    total_enc  = sum(r['montant'] or 0 for r in rows)
    ce_mois    = sum(
        r['montant'] or 0 for r in rows
        if (r['date_paiement'] or '')[:10] >= first_day
    )
    nb         = len(rows)
    panier_moy = int(total_enc / nb) if nb else 0

    paiements_list = []
    for r in rows:
        d = dict(r)
        dp = (r['date_paiement'] or '')[:10]
        if dp >= first_day:
            d['filter_key'] = 'ce_mois'
        elif lm_first <= dp <= lm_last:
            d['filter_key'] = 'mois_precedent'
        else:
            d['filter_key'] = 'ancien'
        paiements_list.append(d)

    stats = {
        'total_encaisse': int(total_enc),
        'ce_mois':        int(ce_mois),
        'nb_paiements':   nb,
        'panier_moyen':   panier_moy,
    }

    return render_template('admin/paiements.html',
                           paiements=paiements_list,
                           stats=stats)


# ─── Monitoring ───────────────────────────────────────────────────────────────

@admin_bp.route('/monitoring')
@admin_required
def monitoring():
    db       = get_db()
    all_live = get_all_peers_status()
    now_ts   = int(datetime.utcnow().timestamp())

    # Résumé containers
    containers_info = _container_summaries()

    # Alertes KA
    ka_alerts_raw = get_keepalive_alerts(DB_PATH)
    # Enrichir avec user_nom depuis DB
    ka_alerts = []
    for a in ka_alerts_raw:
        row = db.execute(
            "SELECT p.id, p.ip_vpn, p.label, p.container, p.actif, u.nom AS user_nom, u.id AS user_id "
            "FROM peers p JOIN users u ON u.id = p.user_id "
            "WHERE p.ip_vpn LIKE ? AND p.actif = 1",
            (a['peer_ip'] + '%',)
        ).fetchone()
        if row:
            d = dict(row)
            d['keepalive'] = all_live.get(a['peer_ip'], {}).get('keepalive', 0)
            ka_alerts.append(d)

    # Tous les peers enrichis
    rows = db.execute("""
        SELECT p.*, u.nom AS user_nom, u.is_banned,
               a.statut AS abo_statut, a.date_fin
        FROM peers p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN abonnements a ON a.user_id = p.user_id
            AND a.date_fin = (SELECT MAX(date_fin) FROM abonnements WHERE user_id = p.user_id)
        ORDER BY p.id
    """).fetchall()
    peers_all = _enrich_peers(rows, all_live)

    # Top peers par trafic reçu
    top_peers = sorted(
        [p for p in peers_all if all_live.get(p['ip_vpn'].split('/')[0], {}).get('rx', 0) > 0],
        key=lambda p: all_live.get(p['ip_vpn'].split('/')[0], {}).get('rx', 0),
        reverse=True
    )[:10]

    # État WireGuard AllowedIPs par clé publique
    current_wg_state = get_current_allowed_ips('amnezia-awg')
    current_wg_state.update(get_current_allowed_ips('amnezia-awg2'))
    for peer in peers_all:
        pk = peer.get('public_key', '')
        if pk and not pk.startswith('MANUAL'):
            allowed = current_wg_state.get(pk, 'inconnu')
            peer['wg_status']      = 'blackhole' if allowed == BLACKHOLE_IP else 'actif'
            peer['wg_allowed_ips'] = allowed
        else:
            peer['wg_status']      = 'no_key'
            peer['wg_allowed_ips'] = None

    return render_template('admin/monitoring.html',
                           containers=containers_info,
                           ka_alerts=ka_alerts,
                           peers_all=peers_all,
                           top_peers=top_peers)


# ─── Logs & Alertes ───────────────────────────────────────────────────────────

@admin_bp.route('/logs')
@admin_required
def logs_page():
    db = get_db()

    alertes = db.execute(
        "SELECT * FROM alertes ORDER BY created_at DESC LIMIT 200"
    ).fetchall()

    logs_raw = db.execute(
        "SELECT * FROM logs_admin ORDER BY created_at DESC LIMIT 500"
    ).fetchall()

    # Catégoriser les logs pour les pills
    logs = []
    for l in logs_raw:
        d = dict(l)
        a = (l['action'] or '').lower()
        if 'paiement' in a or 'montant' in a or 'prolonge' in a:
            d['category'] = 'paiement'
        elif 'peer' in a:
            d['category'] = 'peer'
        elif 'user' in a or 'banni' in a or 'client' in a or 'mdp' in a:
            d['category'] = 'user'
        else:
            d['category'] = 'autre'
        logs.append(d)

    return render_template('admin/logs.html',
                           alertes=alertes,
                           logs=logs)


@admin_bp.route('/logs/mark-read', methods=['POST'])
@admin_required
def mark_alerts_read():
    db = get_db()
    db.execute("UPDATE alertes SET lu = 1")
    db.commit()
    return redirect(url_for('admin_bp.logs_page'))


# ─── Suppression utilisateur ─────────────────────────────────────────────────

def _supprimer_user_data(conn, uid):
    """
    Supprime toutes les données d'un utilisateur (hors vérifications).
    À appeler dans un bloc try/except avec commit/rollback externe.
    Retourne le nom de l'utilisateur supprimé.
    """
    # Récupérer les IPs de ses peers pour supprimer les métriques
    peers = conn.execute(
        "SELECT ip_vpn FROM peers WHERE user_id = ?", (uid,)
    ).fetchall()
    for (ip,) in peers:
        if ip:
            conn.execute("DELETE FROM peer_metrics WHERE peer_ip = ?", (ip,))

    conn.execute("DELETE FROM peers        WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM abonnements  WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM paiements    WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM password_resets WHERE user_id = ?", (uid,))

    for table in ('logs_admin', 'alertes'):
        try:
            col = 'target_user_id' if table == 'logs_admin' else 'user_id'
            conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (uid,))
        except Exception:
            pass

    conn.execute("DELETE FROM users WHERE id = ?", (uid,))


@admin_bp.route('/clients/<int:uid>/supprimer', methods=['POST'])
@admin_required
def supprimer_user(uid):
    """Supprime complètement un utilisateur et toutes ses données liées."""
    conn = sqlite3.connect(DB_PATH)
    try:
        user = conn.execute(
            "SELECT id, nom, is_admin FROM users WHERE id = ?", (uid,)
        ).fetchone()

        if not user:
            flash("Utilisateur introuvable.", "danger")
            return redirect(url_for('admin_bp.clients'))

        if user[2] == 1:
            flash("Impossible de supprimer un administrateur.", "danger")
            return redirect(url_for('admin_bp.clients'))

        nom = user[1]
        _supprimer_user_data(conn, uid)
        conn.commit()

        # Log de l'action (après commit — table logs_admin vidée pour cet uid)
        try:
            conn.execute("""
                INSERT INTO logs_admin (action, detail, admin_id, ip_source)
                VALUES (?, ?, ?, ?)
            """, (
                'Suppression utilisateur',
                f'User #{uid} ({nom}) supprimé définitivement',
                session.get('user_id'),
                request.remote_addr
            ))
            conn.commit()
        except Exception:
            pass

        flash(f"✅ Utilisateur #{uid} ({nom}) supprimé définitivement.", "success")

    except Exception as e:
        conn.rollback()
        flash(f"❌ Erreur lors de la suppression : {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for('admin_bp.clients'))


@admin_bp.route('/clients/supprimer-masse', methods=['POST'])
@admin_required
def supprimer_users_masse():
    """Supprime plusieurs utilisateurs en une seule opération."""
    ids = request.form.getlist('ids')
    if not ids:
        flash("Aucun utilisateur sélectionné.", "warning")
        return redirect(url_for('admin_bp.clients'))

    conn = sqlite3.connect(DB_PATH)
    supprimes = 0
    skipped   = 0
    try:
        for raw_id in ids:
            try:
                uid  = int(raw_id)
                user = conn.execute(
                    "SELECT is_admin, nom FROM users WHERE id = ?", (uid,)
                ).fetchone()
                if not user or user[0] == 1:   # inexistant ou admin → skip
                    skipped += 1
                    continue
                nom = user[1]
                _supprimer_user_data(conn, uid)
                supprimes += 1
            except Exception:
                skipped += 1
                continue

        conn.commit()

        try:
            conn.execute("""
                INSERT INTO logs_admin (action, detail, admin_id, ip_source)
                VALUES (?, ?, ?, ?)
            """, (
                'Suppression en masse',
                f'{supprimes} user(s) supprimés (IDs: {", ".join(ids)})',
                session.get('user_id'),
                request.remote_addr
            ))
            conn.commit()
        except Exception:
            pass

        msg = f"✅ {supprimes} utilisateur(s) supprimé(s) définitivement."
        if skipped:
            msg += f" ({skipped} ignoré(s) — admin ou introuvable)"
        flash(msg, "success")

    except Exception as e:
        conn.rollback()
        flash(f"❌ Erreur : {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for('admin_bp.clients'))


# ─── API JSON ─────────────────────────────────────────────────────────────────

@admin_bp.route('/api/peers/status')
@admin_required
def api_peers_status():
    return jsonify(get_all_peers_status())


@admin_bp.route('/api/kpis')
@admin_required
def api_kpis():
    return jsonify(get_kpis(DB_PATH))


# ─── Recherche IP ─────────────────────────────────────────────────────────────

@admin_bp.route('/ip/rechercher')
@admin_required
def rechercher_ip():
    ip = request.args.get('ip', '').strip()
    if not ip:
        flash('Entrez une adresse IP à rechercher.', 'warning')
        return redirect(url_for('admin_bp.peers'))
    db = get_db()
    bare = ip.split('/')[0]
    peer = db.execute("""
        SELECT p.id, p.ip_vpn, p.label, p.actif, p.container,
               u.id as uid, u.nom,
               COALESCE(a.statut, 'inconnu') as abo_statut
        FROM peers p
        LEFT JOIN users u ON u.id = p.user_id
        LEFT JOIN abonnements a ON a.user_id = p.user_id
        WHERE p.ip_vpn = ? OR p.ip_vpn = ? OR p.ip_vpn LIKE ?
        LIMIT 1
    """, (ip, bare, bare + '/%')).fetchone()
    if peer and peer['uid']:
        flash(
            f"IP {peer['ip_vpn']} → «{peer['label']}» "
            f"({'Actif' if peer['actif'] else 'Suspendu'}) "
            f"— {peer['nom']} ({peer['abo_statut']})",
            'info'
        )
        return redirect(url_for('admin_bp.client_detail', uid=peer['uid']))
    flash(f'Aucun peer trouvé pour l\'IP {ip}.', 'danger')
    return redirect(url_for('admin_bp.peers'))


# ─── Paramètres ───────────────────────────────────────────────────────────────

def _get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r['key']: (r['value'] or '') for r in rows}


@admin_bp.route('/settings')
@admin_required
def settings():
    return render_template('admin/settings.html', s=_get_settings())


# ─── Message individuel ───────────────────────────────────────────────────────

def _send_message_bg(user_dict, subject, message, channel):
    """
    Envoi email + telegram en arrière-plan (thread daemon).
    Réutilise send_email / notify_telegram de app.py (late import, sans circular import
    car les modules sont entièrement chargés avant le premier appel).
    Loggue les erreurs dans la table alertes.
    """
    import sqlite3

    errors = []

    # Late import — safe : app est complètement chargé avant la première requête
    import app as _app

    if channel in ('email', 'both') and user_dict.get('email'):
        html = (
            f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
            f"<p>Bonjour <strong>{user_dict['nom']}</strong>,</p>"
            f"<p>{message.replace(chr(10), '<br>')}</p>"
            f"<hr><small style='color:#888'>VPN Privé</small></div>"
        )
        # Utilise exactement la même fonction que admin_test_email (Brevo API + SMTP fallback)
        with _app.app.app_context():
            ok, err = _app.send_email(user_dict['email'], subject, html)
        if not ok:
            errors.append(f"email: {err}")

    if channel in ('telegram', 'both'):
        text = f"📩 <b>Message → {user_dict['nom']}</b>\n{message}"
        with _app.app.app_context():
            _app.notify_telegram(text)

    if errors:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO alertes (type, message, created_at) VALUES ('error', ?, datetime('now'))",
                (f"envoyer_message #{user_dict['id']}: {'; '.join(errors)}",)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


@admin_bp.route('/clients/<int:uid>/message', methods=['POST'])
@admin_required
def envoyer_message(uid):
    import threading

    subject = request.form.get('subject', '').strip() or 'Information VPN'
    message = request.form.get('message', '').strip()
    channel = request.form.get('channel', 'telegram')

    if not message:
        flash('Message vide.', 'danger')
        return redirect(url_for('admin_bp.client_detail', uid=uid))

    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not user:
        flash('Utilisateur introuvable.', 'danger')
        return redirect(url_for('admin_bp.clients'))

    # Convertir en dict pour le thread (Row SQLite n'est pas thread-safe)
    user_dict = dict(user)

    t = threading.Thread(
        target=_send_message_bg,
        args=(user_dict, subject, message, channel),
        daemon=True
    )
    t.start()

    label = {'email': 'email', 'telegram': 'Telegram', 'both': 'email + Telegram'}.get(channel, channel)
    flash(f"📨 Message envoyé via {label} à {user['nom']}.", 'success')
    return redirect(url_for('admin_bp.client_detail', uid=uid))
