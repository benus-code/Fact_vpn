# DESIGN SYSTEM — VPN Privé Admin Dashboard

Version : 1.0  
Charte validée : dark épuré, accent violet `#bc8cff`, vert `#3fb950` pour AWG 1.x

---

## 1. Palette de couleurs

### Variables CSS (à définir dans `:root`)

```css
:root {
  /* Fonds */
  --bg-app:         #0d1117;   /* Fond global de l'application */
  --bg-sidebar:     #010409;   /* Sidebar plus sombre que le fond app */
  --bg-card:        #161b22;   /* Fond des cards/sections */
  --bg-card-hover:  #1c2128;   /* Fond card au survol */
  --bg-input:       #0d1117;   /* Fond des champs de formulaire */
  --bg-elevated:    #21262d;   /* Badges, inputs hover, surélévation légère */

  /* Bordures */
  --border-subtle:  #30363d;   /* Bordure visible (hover cards, dividers) */
  --border-default: #21262d;   /* Bordure standard (card, input, topbar) */

  /* Texte */
  --text-primary:   #e6edf3;   /* Texte principal, lisible */
  --text-secondary: #8b949e;   /* Labels, metadata */
  --text-muted:     #6e7681;   /* Hints, timestamps, placeholders */

  /* Accents */
  --accent-violet:      #bc8cff;             /* Couleur principale, AWG 2.0, barres données */
  --accent-violet-glow: rgba(188,140,255,0.4); /* Glow boutons/inputs focus */
  --accent-blue:        #58a6ff;             /* Liens, actions secondaires */
  --accent-green:       #3fb950;             /* AWG 1.x, succès, statut actif */
  --accent-orange:      #d29922;             /* Avertissements, expire bientôt */
  --accent-red:         #f85149;             /* Erreur, expiré, suspendu */
}
```

### Tableau de référence visuelle

| Nom            | Hex       | Usage principal                         |
|----------------|-----------|-----------------------------------------|
| bg-app         | `#0d1117` | Fond global                             |
| bg-sidebar     | `#010409` | Sidebar                                 |
| bg-card        | `#161b22` | Cards, sections                         |
| bg-card-hover  | `#1c2128` | Hover état card                         |
| bg-input       | `#0d1117` | Inputs, textareas, selects              |
| bg-elevated    | `#21262d` | Badges fond, hover input                |
| border-subtle  | `#30363d` | Séparateurs, hover cards                |
| border-default | `#21262d` | Bordures standard                       |
| text-primary   | `#e6edf3` | Corps du texte                          |
| text-secondary | `#8b949e` | Labels, metadata                        |
| text-muted     | `#6e7681` | Timestamps, hints                       |
| accent-violet  | `#bc8cff` | Accent principal, AWG 2.0, graphes      |
| accent-blue    | `#58a6ff` | Liens, secondaires                      |
| accent-green   | `#3fb950` | AWG 1.x, actif, succès                  |
| accent-orange  | `#d29922` | Warnings, expiration imminente          |
| accent-red     | `#f85149` | Erreurs, expiré, danger                 |

---

## 2. Typographie

### Import Google Fonts

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Plus+Jakarta+Sans:wght@500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

### Rôles des polices

| Police             | Poids    | Tailles     | Usage                                              |
|--------------------|----------|-------------|----------------------------------------------------|
| Inter              | 400, 500 | 11/12/13/14 | Corps du texte, labels, paragraphes, boutons       |
| Plus Jakarta Sans  | 500, 600 | 22/28       | Titres, valeurs KPI, grands chiffres               |
| JetBrains Mono     | 400, 500 | 10/11/12    | IPs, ports, public keys, statuts, dates, codes     |

### Exemples de tailles

```
Body courant    : Inter 13px / 400    (--text-primary)
Label champ     : Inter 12px / 500    (--text-secondary)
Petit label     : Inter 11px / 400    (--text-muted)
Valeur KPI      : Plus Jakarta 22px / 600 (--text-primary)
Titre section   : Plus Jakarta 14px / 600 (--text-primary)
Code/IP/clé     : JetBrains Mono 11px / 400 (--text-secondary)
Timestamp       : JetBrains Mono 10px / 400 (--text-muted)
```

---

## 3. Espacements

| Contexte                        | Valeur         |
|---------------------------------|----------------|
| Gap vertical entre sections     | 12px (mobile) / 16px (desktop) |
| Padding interne card            | 12–14px        |
| Gap grille KPI                  | 8px            |
| Gap interne formulaire          | 16px           |
| Padding cellule tableau         | 8px 12px       |
| Hauteur ligne tableau           | 36px           |

---

## 4. Composants

### 4.1 KPI Card (mini, grille 2×2 mobile / 4×1 desktop)

**Description :** Carte de statistique principale. Titre en uppercase petit gris, valeur en grand chiffre Plus Jakarta, variation colorée sous la valeur.

```html
<div class="kpi-card">
  <div class="kpi-label">Revenus ce mois</div>
  <div class="kpi-value">4 200 ₽</div>
  <div class="kpi-variation kpi-variation--up">↑ +12% vs mois préc.</div>
</div>

<!-- Variante : variation à la baisse -->
<div class="kpi-card">
  <div class="kpi-label">Clients actifs</div>
  <div class="kpi-value">62 / 84</div>
  <div class="kpi-variation kpi-variation--down">↓ −3 cette semaine</div>
</div>

<!-- Variante : warning -->
<div class="kpi-card kpi-card--warning">
  <div class="kpi-label">Expirent ≤ 7j</div>
  <div class="kpi-value">8</div>
  <div class="kpi-variation" style="color:var(--accent-orange)">⚠ à relancer</div>
</div>
```

**CSS clé :**
```css
.kpi-card {
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 14px;
}
.kpi-label {
  font-family: 'Inter', sans-serif;
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
  margin-bottom: 6px;
}
.kpi-value {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1;
  margin-bottom: 4px;
}
.kpi-variation { font-family: 'Inter', sans-serif; font-size: 11px; }
.kpi-variation--up   { color: var(--accent-green); }
.kpi-variation--down { color: var(--accent-red); }
```

---

### 4.2 Section Card

**Description :** Conteneur de section. Padding 14px, border-radius 8px, fond `--bg-card`.

```html
<div class="section-card">
  <div class="section-card-header">
    <span class="section-card-title">
      <i data-lucide="users" class="icon-sm"></i>
      Clients actifs
    </span>
    <a href="#" class="section-card-link">voir → </a>
  </div>
  <div class="section-card-body">
    <!-- contenu -->
  </div>
</div>
```

**CSS clé :**
```css
.section-card {
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  transition: border-color 150ms;
}
.section-card:hover { border-color: var(--border-subtle); }
.section-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-default);
}
.section-card-title {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}
.section-card-body { padding: 12px 14px; }
.section-card-link {
  font-size: 11px;
  color: var(--accent-blue);
  text-decoration: none;
}
```

---

### 4.3 Badge Interface (A1 / A2 / PV)

**Description :** Badge compact type interface VPN. 9px JetBrains Mono, fond couleur à 15% d'opacité.

```html
<!-- AWG 1.x (vert) -->
<span class="badge-iface badge-iface-a1">A1</span>

<!-- AWG 2.0 (violet) -->
<span class="badge-iface badge-iface-a2">A2</span>

<!-- PiVPN (gris) -->
<span class="badge-iface badge-iface-pv">PV</span>
```

**CSS clé :**
```css
.badge-iface {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  font-weight: 500;
  padding: 1px 5px;
  border-radius: 3px;
  display: inline-block;
  vertical-align: middle;
}
.badge-iface-a1 { background: rgba(63,185,80,0.15); color: var(--accent-green); }
.badge-iface-a2 { background: rgba(188,140,255,0.15); color: var(--accent-violet); }
.badge-iface-pv { background: rgba(139,148,158,0.15); color: var(--text-secondary); }
```

---

### 4.4 Badge Statut (actif / en_attente / expiré / suspendu)

**Description :** Pill de statut. 11px Inter, border-radius 12px.

```html
<span class="badge-status badge-status-actif">Actif</span>
<span class="badge-status badge-status-attente">En attente</span>
<span class="badge-status badge-status-expire">Expiré</span>
<span class="badge-status badge-status-suspendu">Suspendu</span>
```

**CSS clé :**
```css
.badge-status {
  font-family: 'Inter', sans-serif;
  font-size: 11px;
  font-weight: 500;
  padding: 2px 8px;
  border-radius: 12px;
  display: inline-block;
}
.badge-status-actif    { background: rgba(63,185,80,0.15); color: var(--accent-green); }
.badge-status-attente  { background: rgba(210,153,34,0.15); color: var(--accent-orange); }
.badge-status-expire   { background: rgba(248,81,73,0.15); color: var(--accent-red); }
.badge-status-suspendu { background: rgba(110,118,129,0.15); color: var(--text-muted); }
```

---

### 4.5 Bouton Primaire

**Description :** Fond `--accent-violet`, texte `--bg-app` (noir sur violet), border-radius 6px.

```html
<button class="btn-primary">
  <i data-lucide="save" class="icon-sm"></i>
  Enregistrer
</button>

<!-- Variante désactivée -->
<button class="btn-primary" disabled>Traitement…</button>
```

**CSS clé :**
```css
.btn-primary {
  background: var(--accent-violet);
  color: var(--bg-app);
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  font-weight: 500;
  padding: 8px 14px;
  border-radius: 6px;
  border: none;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: opacity 150ms;
}
.btn-primary:hover { opacity: 0.85; }
.btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }
```

---

### 4.6 Bouton Secondaire

**Description :** Fond transparent, bordure `--border-subtle`, texte `--text-primary`.

```html
<button class="btn-secondary">
  <i data-lucide="refresh-cw" class="icon-sm"></i>
  Rafraîchir
</button>
```

**CSS clé :**
```css
.btn-secondary {
  background: transparent;
  color: var(--text-primary);
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  font-weight: 500;
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid var(--border-subtle);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: border-color 150ms, background 150ms;
}
.btn-secondary:hover {
  background: var(--bg-elevated);
  border-color: var(--text-muted);
}
```

---

### 4.7 Bouton Danger

**Description :** Fond rouge à 10% opacité, texte `--accent-red`, bordure `--accent-red`.

```html
<button class="btn-danger">
  <i data-lucide="trash-2" class="icon-sm"></i>
  Supprimer
</button>
```

**CSS clé :**
```css
.btn-danger {
  background: rgba(248,81,73,0.1);
  color: var(--accent-red);
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  font-weight: 500;
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid var(--accent-red);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: background 150ms;
}
.btn-danger:hover { background: rgba(248,81,73,0.2); }
```

---

### 4.8 Input texte

**Description :** Fond `--bg-input`, bordure `--border-default`, focus bordure `--accent-violet` + glow.

```html
<div class="input-group-ds">
  <label class="input-label">Adresse email</label>
  <input type="email" class="input-text" placeholder="user@example.com">
</div>
```

**CSS clé :**
```css
.input-label {
  font-family: 'Inter', sans-serif;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  display: block;
  margin-bottom: 6px;
}
.input-text {
  background: var(--bg-input);
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  padding: 8px 12px;
  border-radius: 6px;
  width: 100%;
  outline: none;
  transition: border-color 150ms, box-shadow 150ms;
}
.input-text:focus {
  border-color: var(--accent-violet);
  box-shadow: 0 0 0 3px rgba(188,140,255,0.2);
}
.input-text::placeholder { color: var(--text-muted); }
```

---

### 4.9 Select

**Description :** Identique à l'input, chevron SVG custom à droite.

```html
<div class="input-group-ds">
  <label class="input-label">Statut</label>
  <select class="input-select">
    <option value="">Tous</option>
    <option value="actif">Actif</option>
    <option value="expire">Expiré</option>
  </select>
</div>
```

**CSS clé :**
```css
.input-select {
  /* mêmes styles que .input-text */
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238b949e' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  padding-right: 30px;
}
```

---

### 4.10 Tableau Dense

**Description :** Lignes 36px, padding cellule 8px 12px, hover fond `--bg-card-hover`, header uppercase 10px.

```html
<table class="table-dense">
  <thead>
    <tr>
      <th>Client</th>
      <th>Interface</th>
      <th>IP VPN</th>
      <th>Statut</th>
      <th>Actions</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Boris Ivanov<br><small class="text-muted-sm">boris@example.com</small></td>
      <td><span class="badge-iface badge-iface-a2">A2</span></td>
      <td><code class="code-sm">10.8.0.12</code></td>
      <td><span class="badge-status badge-status-actif">Actif</span></td>
      <td>
        <button class="btn-icon" title="Voir"><i data-lucide="eye"></i></button>
        <button class="btn-icon" title="Email"><i data-lucide="mail"></i></button>
        <button class="btn-icon btn-icon--danger" title="Suspendre"><i data-lucide="pause"></i></button>
      </td>
    </tr>
  </tbody>
</table>
```

**CSS clé :**
```css
.table-dense {
  width: 100%;
  border-collapse: collapse;
  font-family: 'Inter', sans-serif;
  font-size: 13px;
}
.table-dense thead th {
  font-size: 10px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-default);
  text-align: left;
  white-space: nowrap;
}
.table-dense tbody tr { height: 36px; }
.table-dense tbody tr:hover { background: var(--bg-card-hover); }
.table-dense tbody td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-default);
  color: var(--text-primary);
  vertical-align: middle;
}
.code-sm {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-secondary);
}
.text-muted-sm {
  font-size: 11px;
  color: var(--text-muted);
}
.btn-icon {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  transition: color 150ms, background 150ms;
}
.btn-icon:hover { color: var(--text-primary); background: var(--bg-elevated); }
.btn-icon--danger:hover { color: var(--accent-red); }
```

---

### 4.11 Barre de progression

**Description :** Fond `--bg-elevated`, remplissage `--accent-violet`, hauteur 3px.

```html
<div class="progress-bar-container">
  <div class="progress-bar" style="width: 67%"></div>
</div>

<!-- Variante verte (AWG 1.x) -->
<div class="progress-bar-container">
  <div class="progress-bar progress-bar--green" style="width: 45%"></div>
</div>
```

**CSS clé :**
```css
.progress-bar-container {
  background: var(--bg-elevated);
  border-radius: 2px;
  height: 3px;
  width: 100%;
  overflow: hidden;
}
.progress-bar {
  height: 100%;
  background: var(--accent-violet);
  border-radius: 2px;
  transition: width 300ms ease;
}
.progress-bar--green  { background: var(--accent-green); }
.progress-bar--orange { background: var(--accent-orange); }
.progress-bar--red    { background: var(--accent-red); }
```

---

### 4.12 Timeline Activité

**Description :** Pastille colorée 6px à gauche, texte 12px, métadonnées 10px JetBrains Mono.

```html
<ul class="timeline">
  <li class="timeline-item timeline-item--green">
    <div class="timeline-content">Paiement validé · Boris Ivanov · 600 ₽</div>
    <div class="timeline-meta">il y a 12 min</div>
  </li>
  <li class="timeline-item timeline-item--violet">
    <div class="timeline-content">Peer créé · AWG 2.0 · 10.8.0.43</div>
    <div class="timeline-meta">hier 22:14</div>
  </li>
  <li class="timeline-item timeline-item--orange">
    <div class="timeline-content">Suspension · Anna K. · iptables DROP</div>
    <div class="timeline-meta">12-04-2026</div>
  </li>
  <li class="timeline-item timeline-item--blue">
    <div class="timeline-content">Email envoyé · Relance J-3 · 8 dest.</div>
    <div class="timeline-meta">il y a 2h</div>
  </li>
</ul>
```

**CSS clé :**
```css
.timeline { list-style: none; padding: 0; margin: 0; }
.timeline-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-default);
  position: relative;
}
.timeline-item::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-top: 4px;
  flex-shrink: 0;
}
.timeline-item--green::before  { background: var(--accent-green); }
.timeline-item--violet::before { background: var(--accent-violet); }
.timeline-item--orange::before { background: var(--accent-orange); }
.timeline-item--blue::before   { background: var(--accent-blue); }
.timeline-content {
  font-family: 'Inter', sans-serif;
  font-size: 12px;
  color: var(--text-primary);
  flex: 1;
}
.timeline-meta {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--text-muted);
  white-space: nowrap;
}
```

---

### 4.13 Sidebar Item

**Description :** Padding 10px 14px, icône Lucide 16px, texte 13px. Actif = bordure gauche 3px violet + fond violet à 10%.

```html
<nav class="sidebar-nav">
  <a href="/admin" class="sidebar-item sidebar-item-active">
    <i data-lucide="layout-dashboard" class="icon-sm"></i>
    Vue d'ensemble
  </a>
  <a href="/admin/clients" class="sidebar-item">
    <i data-lucide="users" class="icon-sm"></i>
    Clients
    <span class="sidebar-badge">84</span>
  </a>
  <a href="/admin/paiements" class="sidebar-item">
    <i data-lucide="credit-card" class="icon-sm"></i>
    Paiements
    <span class="sidebar-badge sidebar-badge--orange">3</span>
  </a>
</nav>
```

**CSS clé :**
```css
.sidebar-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  color: var(--text-secondary);
  text-decoration: none;
  border-left: 3px solid transparent;
  transition: color 150ms, background 150ms, border-color 150ms;
}
.sidebar-item:hover {
  color: var(--text-primary);
  background: rgba(139,148,158,0.05);
}
.sidebar-item-active {
  color: var(--accent-violet);
  background: rgba(188,140,255,0.1);
  border-left-color: var(--accent-violet);
}
.sidebar-badge {
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  background: var(--bg-elevated);
  color: var(--text-secondary);
  padding: 1px 6px;
  border-radius: 10px;
}
.sidebar-badge--orange { background: rgba(210,153,34,0.2); color: var(--accent-orange); }
```

---

### 4.14 Topbar

**Description :** 56px de haut, fond `--bg-sidebar`, bordure basse `--border-default`, titre à gauche, actions à droite.

```html
<header class="topbar">
  <div class="topbar-left">
    <button class="topbar-hamburger" id="menuToggle" aria-label="Menu">
      <i data-lucide="menu"></i>
    </button>
    <h1 class="topbar-title">Vue d'ensemble</h1>
  </div>
  <div class="topbar-right">
    <div class="live-indicator">
      <span class="live-dot"></span>
      PROD
    </div>
    <a href="/logout" class="btn-icon" title="Déconnexion">
      <i data-lucide="log-out"></i>
    </a>
  </div>
</header>
```

**CSS clé :**
```css
.topbar {
  height: 56px;
  background: var(--bg-sidebar);
  border-bottom: 1px solid var(--border-default);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  position: sticky;
  top: 0;
  z-index: 100;
}
.topbar-title {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.topbar-left, .topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.topbar-hamburger {
  display: none; /* visible uniquement sur mobile */
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px;
}
@media (max-width: 767px) {
  .topbar-hamburger { display: flex; }
}
```

---

### 4.15 Indicateur Live

**Description :** Pastille 6px verte animée (pulse) + texte uppercase 10px JetBrains Mono.

```html
<div class="live-indicator">
  <span class="live-dot"></span>
  PROD
</div>

<!-- Variante erreur -->
<div class="live-indicator live-indicator--error">
  <span class="live-dot"></span>
  2 DOWN
</div>
```

**CSS clé :**
```css
.live-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 500;
  color: var(--accent-green);
  text-transform: uppercase;
}
.live-indicator--error { color: var(--accent-red); }
.live-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent-green);
  animation: pulse-green 2s infinite;
}
.live-indicator--error .live-dot {
  background: var(--accent-red);
  animation: pulse-red 2s infinite;
}
@keyframes pulse-green {
  0%, 100% { box-shadow: 0 0 0 0 rgba(63,185,80,0.4); }
  50%       { box-shadow: 0 0 0 4px rgba(63,185,80,0); }
}
@keyframes pulse-red {
  0%, 100% { box-shadow: 0 0 0 0 rgba(248,81,73,0.4); }
  50%       { box-shadow: 0 0 0 4px rgba(248,81,73,0); }
}
```

---

## 5. États interactifs

| Élément          | État       | Propriété modifiée                                      |
|------------------|------------|---------------------------------------------------------|
| Card section     | hover      | `border-color` → `--border-subtle` (transition 150ms)  |
| Ligne tableau    | hover      | `background` → `--bg-card-hover`                       |
| Input            | focus      | `border-color` → `--accent-violet` + glow 3px rgba(188,140,255,0.2) |
| Bouton primaire  | hover      | `opacity: 0.85`                                        |
| Bouton secondaire| hover      | `background: --bg-elevated` + border plus claire       |
| Bouton danger    | hover      | `background` intensifié rgba(248,81,73,0.2)           |
| Sidebar item     | hover      | `color: --text-primary` + fond gris subtil             |
| Sidebar item     | actif      | `border-left: 3px violet` + `background: rgba(violet,0.1)` |

---

## 6. Iconographie

- **Bibliothèque** : [Lucide](https://lucide.dev/) via CDN
- **CDN** : `https://unpkg.com/lucide@latest`
- **Initialisation** : `lucide.createIcons()` après chargement du DOM
- **Taille standard** : 16px (sidebar, boutons) — via `class="icon-sm"` (`width:16px; height:16px`)
- **Taille inline** : 14px — via `class="icon-xs"`
- **Taille titre section** : 20px — via `class="icon-md"`
- **Couleur** : héritée du parent (pas de couleur inline)

```html
<!-- CDN dans base_admin.html -->
<script src="https://unpkg.com/lucide@latest"></script>
<!-- En bas du body, après tout le contenu -->
<script>lucide.createIcons();</script>
```

---

## 7. Mobile-first

### Règles obligatoires

1. Tester chaque composant à **375px de large** avant validation
2. Aucun scroll horizontal toléré à 375px
3. Sidebar = **drawer** avec overlay `rgba(0,0,0,0.6)` sur mobile
4. Grille KPI : **2×2** sur mobile (`@media (max-width: 767px)`), **4×1** sur desktop

### Drawer mobile

```css
.sidebar {
  position: fixed;
  left: 0; top: 0; bottom: 0;
  width: 240px;
  transform: translateX(-100%);
  transition: transform 200ms ease;
  z-index: 200;
}
.sidebar.sidebar-open { transform: translateX(0); }
.sidebar-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  z-index: 199;
}
.sidebar-overlay.visible { display: block; }

@media (min-width: 768px) {
  .sidebar {
    position: sticky;
    top: 0;
    transform: translateX(0) !important;
    height: 100vh;
  }
  .sidebar-overlay { display: none !important; }
}
```

### Tableaux sur mobile

Si moins de 4 colonnes essentielles : transformer en cards verticales.

```css
@media (max-width: 575px) {
  .table-dense thead { display: none; }
  .table-dense tbody tr {
    display: block;
    background: var(--bg-card);
    border: 1px solid var(--border-default);
    border-radius: 8px;
    margin-bottom: 8px;
    padding: 10px 12px;
  }
  .table-dense tbody td {
    display: flex;
    justify-content: space-between;
    border: none;
    padding: 4px 0;
  }
  .table-dense tbody td::before {
    content: attr(data-label);
    font-size: 10px;
    color: var(--text-secondary);
    text-transform: uppercase;
    font-weight: 500;
  }
}
```

---

## 8. Validation visuelle

### 5 critères pour juger qu'une page respecte ce système

**1. Palette respectée**
- Aucune couleur hex en dur dans les templates ou dans admin.css (hors déclarations de variables dans `:root`)
- Le fond global est bien `#0d1117`, les cards `#161b22`
- L'accent principal est toujours violet `#bc8cff`, jamais rouge, jamais bleu aléatoire

**2. Typographie correcte**
- Les valeurs KPI utilisent Plus Jakarta Sans ≥ 22px
- Les codes/IPs/clés/timestamps utilisent JetBrains Mono
- Le corps du texte et les labels utilisent Inter
- Aucune police sans-serif générique sans `font-family` explicite

**3. Espacements cohérents**
- Les cards ont toutes un padding de 12–14px
- Le gap entre sections est 12px (mobile) / 16px (desktop)
- Aucun espace aberrant > 24px à l'intérieur d'une card

**4. Badges et statuts harmonieux**
- Les badges A1/A2/PV utilisent les bonnes couleurs (vert/violet/gris)
- Les badges statuts utilisent des pills arrondies (border-radius 12px)
- Cohérence sur toutes les pages (pas de rouge pour AWG 2.0)

**5. Lisible à 375px sans scroll horizontal**
- La sidebar est un drawer, pas une barre fixe sur mobile
- Les tableaux se transforment en cards empilées sur très petit écran
- Les grilles KPI passent en 2×2, pas de débordement
- Les boutons d'action sont assez grands (min 36px) pour être cliquables au doigt

---

*Ce fichier est la source de vérité pour tout développement frontend admin. Avant d'introduire un nouveau composant, documenter ici d'abord.*
