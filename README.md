# docker-compose Icinga stack

docker-compose configuration to start-up an Icinga stack containing
Icinga 2, Icinga Web 2 and Icinga DB.

Ensure you have the latest Docker and docker-compose versions and
then just run `docker-compose -p icinga-playground up` in order to start the Icinga stack.

Icinga Web is provided on **127.0.0.3:8080** and you can access the Icinga 2 API on **127.0.0.3:5665**.
The MariaDB service runs version **11.8** and uses the root password defined by the
`MYSQL_ROOT_PASSWORD` environment variable (default: `icingaroot`).
The default user of Icinga Web is `icingaadmin` with password `icinga` and
the default user of the Icinga 2 API for Web is `icingaweb` with password `icingaweb`.

## Helper scripts and nftables

The repository includes utilities for reproducible deployments and firewall configuration.

- `docker_deploy.sh` uses the Docker Compose plugin v2 to start the stack,
  write log files and generate nftables snippets. Run the script with the path
  to this repository:

  ```
  ./docker_deploy.sh /path/to/docker-compose-icinga
  ```

- `nft_diff.py` compares a global nftables ruleset with the complete ruleset and
  prints only the project-specific rules. The script is used by the deployment
  helper but can be executed manually:

  ```
  ./nft_diff.py GLOBAL.nft COMPLETE.nft > snippet.conf
  ```

Generated nftables examples are stored under `example_nftables/` and can serve as
templates for your environment.

## Upgrading from v1.1.0 to v1.2.0

**v1.2.0** deploys Icinga Web ≥ 2.11.0, Icinga 2 ≥ 2.13.4, Icinga DB ≥ 1.0.0 and Icinga DB Web ≥ 1.0.0.
The Icinga Director is also set up and its daemon started, all in a separate container.

The easiest way to upgrade is to start over, removing all the volumes and
therefore wiping out any configurations you have changed:

`docker-compose -p icinga-playground down --volumes && docker-compose pull && docker-compose -p icinga-playground up --build -d`


## Upgrading from v1.0.0 to v1.1.0

**v1.1.0** deploys Icinga Web 2.9.0 and snapshots of Icinga 2, Icinga DB and Icinga DB Web.

The easiest way to upgrade is to start over, removing all the volumes and
therefore wiping out any configurations you have changed:

`docker-compose down --volumes && docker-compose build --pull && docker-compose -p icinga-playground up -d`
