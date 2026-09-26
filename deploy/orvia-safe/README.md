# Orvia-safe TSP deployment profile

This profile is for the server that already hosts Orvia production workloads.

## Hard boundary

The following are read-only production resources and must never be modified by TSP deployment work:

- `/opt/orvia-funds`
- `/opt/fund-advisor`
- ports `8765` and `8766`
- all Orvia/Fund systemd services, timers, databases, venvs, configs, secrets, caches, backups and existing reverse-proxy routes

TSP uses only:

- root: `/opt/tick-stock-panel`
- data: `/opt/tick-stock-panel/data`
- env: `/opt/tick-stock-panel/.env`
- container: `tick-stock-panel`
- host bind: `127.0.0.1:3018` by default

If there is any conflict, TSP moves or stops. Orvia does not move.

## Why this profile does not build on the server

The upstream Dockerfile is a multi-stage React/Python/Tesseract/Codex build. Building it on the Orvia host can create CPU and memory pressure. Build the fork image in GitHub Actions/GHCR and only pull/run the image on the server.

The profile also intentionally does **not** mount the server's `~/.codex` directory into the container. That prevents the container from inheriting host Codex credentials. TSP Codex-host integration is therefore disabled in this profile unless separately reviewed later.

## First deployment

1. Enable GitHub Actions in the fork and build `ghcr.io/hua329/tick-stock-panel:latest` in GitHub's runners.
2. On the server, perform read-only reconnaissance first. If any Orvia/Fund unit is failed, stop.
3. Copy this repository under `/opt/tick-stock-panel/app` or copy only this deployment profile there.
4. Create `/opt/tick-stock-panel/data`.
5. Copy `.env.example` to `/opt/tick-stock-panel/.env`, fill secrets, and set mode 600.
6. Run the preflight **before any container start**:

   ```bash
   cd /opt/tick-stock-panel/app
   TSP_HOST_PORT=3018 bash deploy/orvia-safe/preflight.sh
   ```

7. If preflight passes, pull and start:

   ```bash
   docker pull ghcr.io/hua329/tick-stock-panel:latest
   docker compose --env-file /opt/tick-stock-panel/.env \
     -f /opt/tick-stock-panel/app/deploy/orvia-safe/docker-compose.yml up -d
   ```

8. Verify only the new endpoint:

   ```bash
   docker ps --filter name=tick-stock-panel
   ss -lntp | grep 3018
   curl -I http://127.0.0.1:3018/
   ```

9. Re-check Orvia read-only. If Orvia changes state, stop TSP and report; do not repair Orvia from this project.

## Initial access

Keep TSP private at first. From the client machine:

```bash
ssh -N -L 13018:127.0.0.1:3018 <server>
```

Then open `http://127.0.0.1:13018/`.

Do not add Nginx, TLS or public routes during the first validation stage.

## Updating

Build/publish the fork image in GitHub Actions, then on the server:

```bash
docker pull ghcr.io/hua329/tick-stock-panel:latest
docker compose --env-file /opt/tick-stock-panel/.env \
  -f /opt/tick-stock-panel/app/deploy/orvia-safe/docker-compose.yml up -d
```

The host data directory is independent of the container image.

## Stop point

Stop immediately if:

- any Orvia/Fund unit is failed;
- protected ports 8765/8766 are missing;
- the chosen TSP port is occupied;
- available memory is below the conservative preflight guardrail;
- Docker changes would require modifying Orvia;
- public routing would require editing an existing Orvia location.

Do not restart or repair Orvia from this repository.
