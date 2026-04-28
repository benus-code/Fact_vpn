# TODO Phase 2 — Fonctionnalités non implémentées

## Données & Backend

### Consommation réelle par peer
- Créer une table `conso_snapshots` (user_id, iface, rx_bytes, tx_bytes, snapshot_at)
- Ajouter un cron (toutes les heures) qui fait `docker exec awg show wg0 transfer` et insère les deltas
- Brancher la section "Top consommateurs" de admin_overview.html sur ces vraies données
- Brancher la colonne "Conso 30j" de admin_clients.html

### API Brevo
- Récupérer le taux d'ouverture moyen via `GET /smtp/statistics/aggregatedReport`
- Récupérer le taux de bounce via `GET /smtp/statistics/aggregatedReport`
- Brancher les KPI "Taux d'ouverture" et "Taux de bounce" dans admin_messages.html
- Migrer l'envoi SMTP vers l'API Brevo (meilleure délivrabilité)

## UX & Interface

### Auto-refresh AJAX du dashboard
- Remplacer le rechargement full-page de admin_systeme.html par un appel AJAX partiel
- Endpoint JSON `/admin/api/systeme` retournant docker_status + active_peers
- Mise à jour du DOM sans rechargement (setInterval + fetch)

### Filtres mobile admin_clients
- Actuellement les filtres sont en ligne horizontale
- Sur mobile < 576px : bouton "Filtres" qui ouvre un bottom sheet natif ou modal Bootstrap
- Appliquer les filtres depuis le sheet

## Exports & Rapports

### Export CSV
- Bouton "Exporter CSV" sur admin_clients.html
- Route Flask `/admin/export/clients.csv` retournant les données en CSV
- Idem pour admin_paiements.html (historique en CSV)

### Export PDF
- Rapport mensuel (revenus + liste clients actifs) en PDF via weasyprint ou pdfkit
- Route `/admin/export/rapport-YYYY-MM.pdf`

## Monitoring

### Grafana / Prometheus
- Installer node_exporter sur le VPS
- Brancher les mini-graphes "Charge VPS" de admin_systeme.html
- CPU, RAM, Network I/O en temps réel

### Logs
- Page `/admin/logs` affichant les 200 dernières lignes de `/var/log/vpn_expire.log`
- Filtres par niveau (INFO, WARNING, ERROR)
- Auto-refresh 10s

## Sécurité

### Rate limiting
- Limiter les tentatives de login (5 tentatives / 15 minutes)
- Limiter les endpoints admin sensibles

### Audit trail
- Table `audit_log` (admin_id, action, details, timestamp)
- Logger : création user, modification abo, suspension peer, suppression peer
- Page `/admin/audit` avec le journal

---
*Généré automatiquement après refonte dashboard Phase 1.*
