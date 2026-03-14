#!/usr/bin/env python3
"""
init_db.py — Initialisation de la base de données + import des peers existants
Lancer UNE SEULE FOIS : python3 init_db.py
"""

import sqlite3
import hashlib
import os

DB_PATH = "/opt/vpn-billing/vpn_billing.db"

# ── Correspondance IP → (pubkey, label_device)
# Extraite de : docker exec amnezia-awg wg show + ta liste JSON
PEERS_DATA = [
    ("10.8.1.1",  "N3R5Op+MRDpJ6pdGBLsMMCyeqxqoz3t+hpxtAZABkEo=", "paul_civ_1"),
    ("10.8.1.2",  "lOkjR7e6UVT9mIREk4y7MgZgrd3cSq/IIMQhi1AdeRg=", "rochelle_phone1"),
    ("10.8.1.3",  "LED/sZtrZO6ckaJ/BmDENZGBGmWOG2gefU6jzuJ5UC4=", "maeva_vpn"),
    ("10.8.1.4",  "juVs6F0AIFatKzOI0sECw1I3DfJebDsQkrhQj3SVl0Q=", "theophane_phone"),
    ("10.8.1.5",  "9lIq6fXroMa+du9B0Qgc5orOcTfBqx1XrVNx859YzEU=", "sisi_phone1"),
    ("10.8.1.6",  "ihOoBiY3FYmeqI495Sr1Jpl6+wPnr5eiKg1QG5KsXw0=", "sisi_phone2"),
    ("10.8.1.7",  "MQguOSEiVzhDnhEKzpfmOnhDf4lX3yyfl5SKfGphOAQ=", "angess_phone1"),
    ("10.8.1.8",  "JA9eH94sxoqnNxqDtA10TedWOaS2URgB9Ksc5Opw0jk=", "francis_phone"),
    ("10.8.1.9",  "s+JT+RyqxPyUTi3z6I3j/dQxOMFVTXTZ8EiE3cqK9zQ=", "angess_phone2"),
    ("10.8.1.10", "Fe6QdloJQ7cCS1QGf6rKyg3mHUDcmTKQ8zYzx4eyCyk=", "daryl_phone"),
    ("10.8.1.11", "CLxZ8crSDiMf8jFmLOBmuC+amueqTBQ9OIrTI6YtukQ=", "aurele_phone1"),
    ("10.8.1.12", "AiAXEGprF/CHJlt0hFdpTsa0eUsEEH/+GeU4Yd9n4Dw=", "devaria_phone1"),
    ("10.8.1.13", "JNlaGgxkFFdV8pfM7gUmrqXzxINNbzumWEffW7U2RHI=", "aurele_phone2"),
    ("10.8.1.14", "xW/maHApxk4AfrpNU0gYNxnkkmbm6Ek+QBJm203mUGw=", "angess_phone3"),
    ("10.8.1.15", "VeQ8+YX/Eh/BT/0qS+FKpE+sDmyB54kUs2IFCWkNBxY=", "ekotto_phone"),
    ("10.8.1.16", "UfL7tHQHV4SHI+gSdTjTc3xHbjBOWWH6jjI2EgzEoX8=", "christ_phone"),
    ("10.8.1.17", "egQvMQ3/Db+tZr8kRUu+Q2ebVcX+0L9h9zQSh7qy0Ug=", "rochelle_phone2"),
    # 10.8.1.18 → paul_civ_18 : dans la liste mais aucun peer WireGuard (config jamais utilisée)
    ("10.8.1.19", "Wx5qPaW4G9B7Brw7OGiu+ewFqWLiZ+gPEFC2YpXftXk=", "lobe_phone"),
    ("10.8.1.20", "xhnwHDsEBOVKUKP+ws95w8Lwu7P7y0fdLRxVkxr7CR8=", "rostand_phone1"),
    ("10.8.1.21", "ICj5Y7JX500sWHeoue7xsCOIhnISr438HdPRplseiX4=", "boris_f_phone"),
    ("10.8.1.22", "0vRjl3qkk4oR6m8Zzz97BgeN42GbMuwCZ61xZ86jvlA=", "arol_vpn"),
    ("10.8.1.23", "I3U/j6Zf8aB15U5mFm/J3l0CxgUia/O0nn3HzO9H2h4=", "melissa_phone"),
    ("10.8.1.24", "IcOLX9DBs3JIk+Y2vl/iQna7y0/194g+kHMItLQtAWg=", "mike_phone"),
    ("10.8.1.25", "X9TilzQXtDbeI361PC9yFykNfhAuXLTHax8ZJyPmmk4=", "sonia_phone"),
    ("10.8.1.26", "ZWhslLPGBiXMt33qxPsZcB8UuFRNaxoxY3Hd6Ob2ZUo=", "beatrice_phone"),
    ("10.8.1.27", "KkQfpwhMr2AefRKjwXbCYIH9qg5lF+yeN6f6cBJMPEo=", "stanislass_phone1"),
    ("10.8.1.28", "QSuy4tee9VbMAlgaJ45GyTxcgseFiUFipFFhWCN8YWw=", "joelle_phone"),
    ("10.8.1.29", "b+186WVASmt3+sM8f6Pz/2XNt4WOlit54o0YAaw7LUI=", "eleonore_vpn"),
    ("10.8.1.30", "TC24ioAVKb0yJ4OdcbNzVOVkYQjRGUWeu5vRZR86gF8=", "stephy_phone"),
    ("10.8.1.31", "/zSXgpO4FKyGKF2ibBAnxzr11XV9cTPjjXYFa1mNFn0=", "omega_phone"),
    ("10.8.1.32", "6749bIb9mFMVuai30JqfE+0kuC43URQp1swZIhiDzVE=", "rostand_phone2"),
    ("10.8.1.33", "9mLbhjlr6i/xj0Xt45Vo8KMIaGs0hZKE5E7gvLiz7yM=", "maxim_phone1"),
    ("10.8.1.34", "auETyqPcVD06Dgx3Tybb7S+T8G7nn/DHCrheI3sDgBg=", "ledy_phone"),
    ("10.8.1.35", "M9M8ijo0FMGVrMDuooAJW4o1nUlTkrScrEzyKzpFcnI=", "rochelle_phone3"),
    ("10.8.1.37", "iyoNqqEnlKCllHL4+EuIN4jCoif/kF98CQlDKrLdh0U=", "phillipe_phone"),
    ("10.8.1.38", "DYXT9YV0S0iTvh6GRWoj9V3zFpIKQw/YFrLZRuK3nwE=", "roberto_phone"),
    ("10.8.1.39", "gPX//LLVDWpTQZkG3iKGJ/BBknsv3CIoafiKxFGpPXQ=", "michelle_phone"),
    ("10.8.1.40", "KKs5JNa0KEm89jtk+f/UVoN0lJdH4Cg29O2/2Xuu+VU=", "marcel_phone"),
    ("10.8.1.41", "g+cvWZOnQOTJqXHkEbBInPwtiQTDsPpRP3YKORIEeFU=", "inconnu_41"),  # ← à identifier !
    ("10.8.1.42", "WB1qLrDM7Yl2bJGoasG2oa3ZXqRrWk1pZUK0uY8ttSY=", "stanislass_phone2"),
    ("10.8.1.43", "sS/BgOgNrSBxeGDeqJGwaOItSEaan4zduxmwvUiTkAk=", "merveille_phone"),
    ("10.8.1.44", "eZNerxbPWPXJX1tQuTTl1dzXLU4BJkaG3p8gs9hpew0=", "stanislass_phone3"),
    ("10.8.1.45", "2dUAiV6BFEB/QcERsN1e4FhR9K+0DKLdsEikd7ubOjE=", "fortuna_civ"),
    ("10.8.1.46", "gUGDMJ0ADUCnenc+Dnl0+t2ppJzF5d8wTCXnzsLCyB0=", "stanislass_phone4"),
    ("10.8.1.47", "03E2T53vS1kWiNN2AtTSEAKjE4ewtZZERZ1LHc6MRSU=", "devaria_phone2"),
    ("10.8.1.49", "18TjkmYJfaQYXTdeh/wVzTA6aK0Qq62u8Dpqj9Ol8iI=", "christiane_iphone"),
    ("10.8.1.50", "a1cMbHypTKuU++Cds+nknNQISMKtqyUasaaf6cbFrBg=", "paul_os_ma"),
    ("10.8.1.51", "WGpgwpNtwkpTX6lU6RaLLxY2J6KcivGGM9nLuz3QwAc=", "maxim_phone2"),
    ("10.8.1.52", "ZpRjG2XHNgkmFKrMNVwT1SCfo83tKfUtE3ZyOYgSi1g=", "alex_phone"),
]

# ── Regroupement par personne réelle pour la facturation
# format : (nom_affichage, email_login, [liste des ip_vpn rattachées])
USERS_DATA = [
    ("Paul",        "paul@vpn.local",        ["10.8.1.1", "10.8.1.50"]),
    ("Rochelle",    "rochelle@vpn.local",     ["10.8.1.2", "10.8.1.17", "10.8.1.35"]),
    ("Maeva",       "maeva@vpn.local",        ["10.8.1.3"]),
    ("Theophane",   "theophane@vpn.local",    ["10.8.1.4"]),
    ("Sisi",        "sisi@vpn.local",         ["10.8.1.5", "10.8.1.6"]),
    ("Angess",      "angess@vpn.local",       ["10.8.1.7", "10.8.1.9", "10.8.1.14"]),
    ("Francis",     "francis@vpn.local",      ["10.8.1.8"]),
    ("Daryl",       "daryl@vpn.local",        ["10.8.1.10"]),
    ("Aurele",      "aurele@vpn.local",       ["10.8.1.11", "10.8.1.13"]),
    ("Devaria",     "devaria@vpn.local",      ["10.8.1.12", "10.8.1.47"]),
    ("Ekotto",      "ekotto@vpn.local",       ["10.8.1.15"]),
    ("Christ",      "christ@vpn.local",       ["10.8.1.16"]),
    ("Lobe",        "lobe@vpn.local",         ["10.8.1.19"]),
    ("Rostand",     "rostand@vpn.local",      ["10.8.1.20", "10.8.1.32"]),
    ("Boris F",     "boris@vpn.local",        ["10.8.1.21"]),
    ("Arol",        "arol@vpn.local",         ["10.8.1.22"]),
    ("Melissa",     "melissa@vpn.local",      ["10.8.1.23"]),
    ("Mike",        "mike@vpn.local",         ["10.8.1.24"]),
    ("Sonia",       "sonia@vpn.local",        ["10.8.1.25"]),
    ("Beatrice",    "beatrice@vpn.local",     ["10.8.1.26"]),
    ("Stanislass",  "stanislass@vpn.local",   ["10.8.1.27", "10.8.1.42", "10.8.1.44", "10.8.1.46"]),
    ("Joelle",      "joelle@vpn.local",       ["10.8.1.28"]),
    ("Eleonore",    "eleonore@vpn.local",     ["10.8.1.29"]),
    ("Stephy",      "stephy@vpn.local",       ["10.8.1.30"]),
    ("Omega",       "omega@vpn.local",        ["10.8.1.31"]),
    ("Maxim",       "maxim@vpn.local",        ["10.8.1.33", "10.8.1.51"]),
    ("Ledy",        "ledy@vpn.local",         ["10.8.1.34"]),
    ("Phillipe",    "phillipe@vpn.local",     ["10.8.1.37"]),
    ("Roberto",     "roberto@vpn.local",      ["10.8.1.38"]),
    ("Michelle",    "michelle@vpn.local",     ["10.8.1.39"]),
    ("Marcel",      "marcel@vpn.local",       ["10.8.1.40"]),
    ("Inconnu .41", "inconnu41@vpn.local",    ["10.8.1.41"]),  # ← à identifier !
    ("Merveille",   "merveille@vpn.local",    ["10.8.1.43"]),
    ("Fortuna",     "fortuna@vpn.local",      ["10.8.1.45"]),
    ("Christiane",  "christiane@vpn.local",   ["10.8.1.49"]),
    ("Alex",        "alex@vpn.local",         ["10.8.1.52"]),
]

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ── Tables
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        nom           TEXT NOT NULL,
        email         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin      INTEGER DEFAULT 0,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS peers (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
        label      TEXT NOT NULL,
        public_key TEXT UNIQUE NOT NULL,
        ip_vpn     TEXT UNIQUE NOT NULL,
        actif      INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS abonnements (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
        date_debut  DATE,
        date_fin    DATE,
        montant     REAL DEFAULT 5.0,
        statut      TEXT DEFAULT 'actif'
    );

    CREATE TABLE IF NOT EXISTS paiements (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER REFERENCES users(id),
        montant         REAL NOT NULL,
        mois_prolonges  INTEGER DEFAULT 1,
        note            TEXT,
        date_paiement   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        valide          INTEGER DEFAULT 0
    );
    """)

    # ── Compte admin
    c.execute("""
        INSERT OR IGNORE INTO users (nom, email, password_hash, is_admin)
        VALUES (?, ?, ?, 1)
    """, ("Admin", "admin@vpn.local", hash_password("admin1234")))
    print("[OK] Compte admin créé  →  email: admin@vpn.local  /  mdp: admin1234")
    print("     ⚠  Change le mot de passe après la première connexion !\n")

    # ── Index IP → peer data
    peer_map = {ip: (pubkey, label) for ip, pubkey, label in PEERS_DATA}

    # ── Import des utilisateurs et leurs peers
    for nom, email, ips in USERS_DATA:
        mdp_temp = nom.lower().replace(" ", "") + "123"
        c.execute("""
            INSERT OR IGNORE INTO users (nom, email, password_hash)
            VALUES (?, ?, ?)
        """, (nom, email, hash_password(mdp_temp)))

        c.execute("SELECT id FROM users WHERE email = ?", (email,))
        user_id = c.fetchone()[0]

        # Abonnement (sans date de fin — admin définira)
        c.execute("""
            INSERT OR IGNORE INTO abonnements (user_id, statut)
            VALUES (?, 'actif')
        """, (user_id,))

        for ip in ips:
            if ip in peer_map:
                pubkey, label = peer_map[ip]
                c.execute("""
                    INSERT OR IGNORE INTO peers (user_id, label, public_key, ip_vpn)
                    VALUES (?, ?, ?, ?)
                """, (user_id, label, pubkey, ip))
            else:
                print(f"  ⚠  IP {ip} → aucun peer WireGuard trouvé (peer inexistant)")

        print(f"[OK] {nom:20s} → {len(ips)} device(s) → mdp temp: {mdp_temp}")

    conn.commit()
    conn.close()
    print(f"\n✅  Base de données créée : {DB_PATH}")
    print("   Tu peux maintenant lancer : python3 app.py")

if __name__ == "__main__":
    init_database()
