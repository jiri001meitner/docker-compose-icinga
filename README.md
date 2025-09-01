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
and `docker compose down -v` to remove the deployed configuration while keeping
the nftables rules in `etc/nftables.d/`. Verify the services are healthy with
`docker ps` or `docker compose ps`.

Icinga Web is provided on **127.0.0.3:8080** and you can access the Icinga 2 API on **127.0.0.3:5665**.
The MariaDB service runs version **11.8** and uses the root password defined by the
`MYSQL_ROOT_PASSWORD` environment variable (default: `icingaroot`).
The default user of Icinga Web is `icingaadmin` with password `icinga` and
the default user of the Icinga 2 API for Web is `icingaweb` with password `icingaweb`.

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
  helper but can be executed manually:

  ```
  ./nft_diff.py GLOBAL.nft COMPLETE.nft > snippet.conf
  ```

Generated nftables examples are stored under `example_nftables/` and can serve as
templates for your environment.

The deployment script writes logs to `logs/` and nftables snippets to `etc/nftables.d/`,
both of which are excluded from version control.

## Upgrading from v1.1.0 to v1.2.0

**v1.2.0** deploys Icinga Web ≥ 2.11.0, Icinga 2 ≥ 2.13.4, Icinga DB ≥ 1.0.0 and Icinga DB Web ≥ 1.0.0.
The Icinga Director is also set up and its daemon started, all in a separate container.

The easiest way to upgrade is to start over, removing all the volumes and
therefore wiping out any configurations you have changed:

`docker compose down --volumes && docker compose pull && docker compose up --build -d`


## Upgrading from v1.0.0 to v1.1.0

**v1.1.0** deploys Icinga Web 2.9.0 and snapshots of Icinga 2, Icinga DB and Icinga DB Web.

The easiest way to upgrade is to start over, removing all the volumes and
therefore wiping out any configurations you have changed:

`docker compose down --volumes && docker compose build --pull && docker compose up -d`
