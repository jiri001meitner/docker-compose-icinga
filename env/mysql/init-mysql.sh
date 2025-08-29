#!/bin/sh
set -e

create_database_and_user() {
  DB="$1"
  USER="$2"
  PASSWORD="$3"

  mariadb --user root --password="${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${USER}'@'%' IDENTIFIED BY '${PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB}\`.* TO '${USER}'@'%';
FLUSH PRIVILEGES;
SQL
}

create_database_and_user director  director  "${ICINGA_DIRECTOR_MYSQL_PASSWORD:-director}"
create_database_and_user icingadb  icingadb  "${ICINGADB_MYSQL_PASSWORD:-icingadb}"
create_database_and_user icingaweb icingaweb "${ICINGAWEB_MYSQL_PASSWORD:-icingaweb}"
