# MemeDL

Un lien de post partagé, un fichier dans le dossier « Memes » du NAS.

Le moteur est **gallery-dl**. Ce dépôt contient le service d'entrée, l'abonné ntfy et les manifestes k3s. Il a été extrait de `rouedad-infra` (branche `feat/memes-k3s`, `kubernetes/k3s/apps/memes/`) pour devenir un projet autonome sous `Elektro121`.

## État au 21 novembre 2026

- Code préparé localement, **pas encore poussé** sur GitHub.
- Pas déployé : la branche d'origine n'est pas mergée sur `master`.
- **Support X/Twitter validé le 21 novembre 2026** sur trois posts publics réels. `gallery-dl` récupère les images. Un échec ponctuel sur un autre post venait du guest token, pas d'une interdiction liée à l'IP. Pour les vidéos et les GIFs, le service utilise `yt-dlp`.

## Composants

- `memedl.py` — service HTTP (stdlib uniquement) : `POST /hook` accepte du texte brut, `{"url": "..."}` ou un formulaire `url=...`. File d'attente sériale, renommage atomique depuis un dossier temporaire, galerie HTML, `GET /f/<nom>`, `GET /healthz`. Authentification par `?token=` ou en-tête `X-Token`.
- `memedl-ntfy.py` — abonné au flux ntfy : reprise au dernier identifiant de message traité, extraction de la première URL, envoi vers `/hook`.
- `Dockerfile`, `deployment.yaml`, `service.yaml`, `ingress.yaml`, `pvc.yaml`, `kustomization.yaml` — déploiement k3s, volume NAS, Ingress Traefik sur un hôte interne.

## Formats pris en charge

Trois cas validés le 21 novembre 2026 sur des posts publics réels :

- **Photos** — `gallery-dl` récupère l'original. Vérifié : JPEG 1254 × 1674.
- **Vidéos avec son** — `yt-dlp -f "bv*+ba/b"` demande la meilleure vidéo et le meilleur audio, puis fusionne. Vérifié : H.264 394 × 360 + AAC.
- **GIFs** — X sert un MP4 muet. Le service détecte l'absence de piste audio avec `ffprobe`, puis convertit en vrai `.gif` avec `ffmpeg` (15 images/s, 480 px de large maximum). Vérifié : GIF89a 252 × 450. Si `ffmpeg` échoue, le MP4 d'origine est conservé.

`yt-dlp` est aussi utilisé en premier sur les URLs X. S'il ne trouve rien, le service retombe sur `gallery-dl` pour les posts image seule.

## Configuration

Variables lues par `memedl.py` :

- `MEMEDL_DIR` — dossier de destination (défaut `~/memes`)
- `MEMEDL_PORT` — défaut `8765`
- `MEMEDL_BIND` — défaut `127.0.0.1`
- `MEMEDL_TOKEN` — généré et affiché au démarrage s'il est absent
- `MEMEDL_COOKIES` — fichier `cookies.txt` au format Netscape

Le service transmet `MEMEDL_COOKIES` aux URLs `instagram.com` et `x.com` / `twitter.com` quand le fichier existe. Aucun cookie n'était nécessaire pour les trois posts publics testés.

## Sécurité

- Le token dans l'URL est l'authentification : ne pas partager le lien de la galerie.
- Lier `127.0.0.1` et laisser le reverse proxy qui termine TLS exposer le service.
- Ne jamais committer de cookies ni de token dans ce dépôt.
