# docker-compose Icinga stack

Docker Compose configuration to start up an Icinga stack containing
Icinga 2, Icinga Web 2 and Icinga DB. Unlike the classic docker-compose
setup for Icinga, this repository is tailored for deployments secured
with **nftables** and ships helper scripts to generate firewall rules.

Ensure you have the latest Docker and the Docker Compose plugin v2.

## Recommended deployment

```bash
mkdir -p docker
git clone https://github.com/jiri001meitner/docker-compose-icinga.git icinga
cd docker/icinga
./docker_first_deploy.sh ./
```

The `docker_first_deploy.sh` script prepares the host for the first run. It stops
and removes all existing containers together with their Docker networks to obtain a clean
ruleset, generates nftables snippets and installs them to
`/etc/nftables.d/2_docker_global.conf` and `/etc/nftables.d/3_docker_icinga.conf`,
which are included via `/etc/nftables.conf`. The stack is then started. Use
`docker compose down` to stop Icinga, `docker compose up -d` to start it again
and `docker compose down -v` to remove the deployed configuration.
If nftables rules were deployed for an application, for example, using the `docker_first_deploy.sh` script, note that they will not be removed from `/etc/nftables.d/` even when running `docker compose down -v`.
Verify the services are healthy with `docker ps` or `docker compose ps`.

### Configuration (`.env`)

Copy `.env.example` to `.env` (`chmod 600`) and fill it in before the first
start. All passwords are required; `docker compose` refuses to start without
them, so no well-known default password ever ends up in a deployment.

| variable | purpose |
|---|---|
| `MYSQL_ROOT_PASSWORD` | MariaDB root (version **11.8**) |
| `ICINGADB_MYSQL_PASSWORD`, `ICINGAWEB_MYSQL_PASSWORD` | database users of Icinga DB and Icinga Web 2 |
| `ICINGAWEB_ICINGA2_API_USER_PASSWORD` | Icinga 2 API user `icingaweb` |
| `ICINGAWEB_ADMIN_PASSWORD` | Icinga Web 2 login `icingaadmin` |
| `SOCKET_UID`, `SOCKET_GID`, `SOCKET_DIR` | owner and directory of the UNIX sockets |
| `V4_PREFIX`, `V6_PREFIX` | network prefixes (default `172.29.0`, `fd00:d0c:29`) |

The database passwords are applied only when MariaDB initialises an empty
volume (`env/mysql/init-mysql.sh`), and the API user only when
`conf.d/icingaweb-api-user.conf` does not exist yet. Changing them later means
`ALTER USER` in MariaDB and editing that file in the `icinga2` volume.

### Access via UNIX sockets

No port is published. Two socat sidecars expose Icinga Web 2 as
`$SOCKET_DIR/web.sock` (→ 8080) and the Icinga 2 API as `$SOCKET_DIR/api.sock`
(→ 5665) for a reverse proxy (e.g. Caddy `reverse_proxy unix//run/icinga/web.sock`).
Create the directory before the containers start, e.g. with systemd-tmpfiles:
`d /run/icinga 2770 1000 caddy -`.

The sidecars run in the network namespace of their service
(`network_mode: service:…`). Whenever `icingaweb` or `icinga2` is recreated,
recreate the sidecars too, otherwise the proxy gets `EOF`/502:

```bash
docker compose up -d --force-recreate --no-deps icingaweb-socket icinga2-socket
```

### Notes

- `icinga2` gets `net.ipv4.ping_group_range` for the `icinga` group (GID 5665);
  without it `check_ping` fails with `Could not interpret output from ping command`.
- Icinga DB does not migrate its schema itself. After a newer `icinga/icingadb`
  image (`unexpected database schema version: vN (expected vM)`) apply the files
  from `schema/mysql/upgrades/` of the matching Icinga DB release, e.g.
  `docker exec -i <mysql> sh -c 'mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" icingadb' < 1.6.0.sql`.
- Icinga Director is not part of the stack.

## Helper scripts and nftables

The repository includes utilities for reproducible deployments and firewall configuration.

- `docker_first_deploy.sh` prepares the host for the first run, stops and
  removes existing containers together with their Docker networks, writes log
  files and generates
  nftables snippets. It installs the snippets to
  `/etc/nftables.d/2_docker_global.conf` and `/etc/nftables.d/3_docker_icinga.conf`
  (included via `/etc/nftables.conf`) and must be used for the initial
  deployment. Run the script from the repository root:

  ```
  ./docker_first_deploy.sh ./
  ```

- `nft_diff.py` compares a global nftables ruleset with the complete ruleset and
  prints only the project-specific rules. The script is used by the deployment
  helper but can be executed manually, for example:

  ```
  ./nft_diff.py nft_global.conf nft_with_app.conf > nft_app_specific_diff.conf
  ```

Generated nftables examples are stored under `example_nftables/` and can serve as
templates for your environment.

The deployment script writes logs to `logs/` and nftables snippets to `etc/nftables.d/`,
both of which are excluded from version control.

The nftables rules are automatically deployed and activated in /etc/nftables.conf.  The script assumes nftables, not iptables, is your primary firewall. Do not use on a system without an active and enabled nftables.service.
