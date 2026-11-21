# MemeDL

Un lien de post partagé, un fichier dans le dossier « Memes » du NAS.

Le moteur est **gallery-dl**. Ce dépôt contient le service d'entrée, l'abonné ntfy et les manifestes k3s. Il a été extrait de `rouedad-infra` (branche `feat/memes-k3s`, `kubernetes/k3s/apps/memes/`) pour devenir un projet autonome sous `Elektro121`.

## État au 21 novembre 2026

- Code préparé localement, **pas encore poussé** sur GitHub.
- Pas déployé : la branche d'origine n'est pas mergée sur `master`.
- **Support X/Twitter à prouver.** Depuis l'hôte sandbox (IP de datacenter), `gallery-dl -g` sur un post x.com répond `[twitter][info] Requesting guest token` puis `[twitter][error] 'Unavailable'`. Ce résultat est non concluant : la même limite peut venir de l'IP de sortie. La sonde qui fait autorité doit tourner depuis le WAN du foyer ou depuis le pod déployé.

## Composants

- `memedl.py` — service HTTP (stdlib uniquement) : `POST /hook` accepte du texte brut, `{"url": "..."}` ou un formulaire `url=...`. File d'attente sériale, renommage atomique depuis un dossier temporaire, galerie HTML, `GET /f/<nom>`, `GET /healthz`. Authentification par `?token=` ou en-tête `X-Token`.
- `memedl-ntfy.py` — abonné au flux ntfy : reprise au dernier identifiant de message traité, extraction de la première URL, envoi vers `/hook`.
- `Dockerfile`, `deployment.yaml`, `service.yaml`, `ingress.yaml`, `pvc.yaml`, `kustomization.yaml` — déploiement k3s, volume NAS, Ingress Traefik sur un hôte interne.

## Formats attendus

Trois cas à couvrir pour les posts X/Twitter :

- **Photos** — fichiers originaux, y compris les carrousels. Déjà couvert par `gallery-dl`.
- **GIFs** — X les stocke comme de courtes vidéos MP4 en boucle, sans piste audio. Pour obtenir un vrai fichier `.gif`, il faut convertir le MP4 avec `ffmpeg` (présent sur l'hôte). À valider sur un post réel.
- **Vidéos avec son** — sélectionner un format qui garde l'audio, pas seulement la vidéo. `yt-dlp` permet de demander « meilleure vidéo + meilleur audio » avec repli sur le meilleur format progressif. À valider sur un post réel.

Les deux derniers points ne sont pas encore implémentés : la sonde sur un vrai post manque.

## Configuration

Variables lues par `memedl.py` :

- `MEMEDL_DIR` — dossier de destination (défaut `~/memes`)
- `MEMEDL_PORT` — défaut `8765`
- `MEMEDL_BIND` — défaut `127.0.0.1`
- `MEMEDL_TOKEN` — généré et affiché au démarrage s'il est absent
- `MEMEDL_COOKIES` — fichier `cookies.txt` au format Netscape

Le service ne transmet `MEMEDL_COOKIES` que pour les URL `instagram.com`. L'extension à `x.com` / `twitter.com` n'est pas faite tant que la sonde n'a pas montré qu'elle était nécessaire : ne pas ajouter de code sur une hypothèse.

## Sécurité

- Le token dans l'URL est l'authentification : ne pas partager le lien de la galerie.
- Lier `127.0.0.1` et laisser le reverse proxy qui termine TLS exposer le service.
- Ne jamais committer de cookies ni de token dans ce dépôt.
