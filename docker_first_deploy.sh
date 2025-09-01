#!/usr/bin/env bash
STDERR(){ cat - 1>&2; }

IPV6_ENABLE='false'

help1(){ { echo "usage:"; echo "${0##*/} /path/to/dir/"; } | STDERR; exit 1; }
help2(){ echo "This script requires the docker compose plugin version 2 to be installed." | STDERR; exit 2; }
help3(){ echo "The directory does not contain a docker-compose.yml file." | STDERR; exit 3; }
help4(){ echo "The docker-compose.yml file contains errors." | STDERR; exit 4; }
help5(){ echo "The nftables package appears to be missing. Please install it first." | STDERR; exit 5; }
help6(){ { echo "The jq package appears to be missing.";
         echo "This script uses it to modify /etc/docker/daemon.json. Please install it first."; } | STDERR; exit 6; }
help7(){ echo "You chose not to continue, exiting." | STDERR; exit 7; }
help8(){ echo "The 'nftables.service' must be enabled and active." | STDERR; exit 8; }


(( $# == 1 )) || help1

FULL_PATH="$(realpath -e -- "$1")"
[[ -d "$FULL_PATH" ]] || help1
cd -- "$FULL_PATH"||help1

[[ -f docker-compose.yml ]] || help3

if ! ver="$(docker compose version --short)"; then
  help2
fi
[[ "${ver%%.*}" == "2" ]] || help2

docker compose config >/dev/null || help4

[[ -f /etc/nftables.conf ]] || help5
if ! sudo which nft|grep . -q; then help5; fi

if ! jq --version|grep . -q; then help6; fi

NFTABLES_STATUS="$(systemctl status nftables.service)"
if ! echo "${NFTABLES_STATUS}"|grep -q 'enabled; preset: enabled'; then
   help8
fi
if ! echo "${NFTABLES_STATUS}"|grep -q 'Active: active'; then
   help8
fi

APP_NAME="$(basename "${FULL_PATH}")"
LOG_DIR="${FULL_PATH}/logs/"
LOG_FILE="${LOG_DIR}/stdout_stderr.log"
NFTABLES="${FULL_PATH}/etc/nftables.d/"
NFT_DOCKER_GLOBAL="${NFTABLES}/2_docker_global.conf"
NFT_APP_TMP="${NFTABLES}/3_docker_${APP_NAME}_tmp.conf"
NFT_APP_FINAL="${NFTABLES}/3_docker_${APP_NAME}.conf"
DOCKER_SETTINGS_OLD="$(sudo cat /etc/docker/daemon.json|jq -c --||echo '{}')"
if [[ "${IPV6_ENABLE,,}" == "true" || "${IPV6_ENABLE,,}" == "yes" ]]; then
  DOCKER_SETTINGS_TEMP="$(
  printf '%s' "${DOCKER_SETTINGS_OLD}" | jq -c '
    .experimental = true
    | .iptables    = true
    | .ip6tables   = true
    | .ipv6        = true
    | (if has("fixed-cidr-v6") then . else . + {"fixed-cidr-v6":"fd00:dead:beef::/64"} end)
  '
)"; else
  DOCKER_SETTINGS_TEMP="$(
  printf '%s' "${DOCKER_SETTINGS_OLD}" | jq -c '
    .experimental = true
    | .iptables    = true
    | .ip6tables   = false
    | .ipv6        = false
    | (if has("fixed-cidr-v6") then . else . + {"fixed-cidr-v6":"fd00:dead:beef::/64"} end)
  '
)"
fi
DOCKER_SETTINGS_NEW="$(
  printf '%s' "${DOCKER_SETTINGS_TEMP}" | jq -c '
    .iptables  = false
    | .ip6tables = false
  '
)"

echo "Warning: This script will stop all running containers and then deploy nftables rules for the built application.  It will also generate an archive of logs from the execution and containers for debugging purposes."
echo "${HOSTNAME}"
read -erp 'Continue? ' -i 'yes' continue
if ! [[ ${continue} == 'yes' ]]; then
   help7
fi

rm -rf "${LOG_DIR}" "${NFTABLES}"
mkdir -p "${LOG_DIR}" "${NFTABLES}"

# všechno odteď -> obrazovka + LOG_FILE (append)
exec > >(tee -a "${LOG_FILE}") 2>&1

TD()
{
printf '%(%Y-%m-%d-%H:%M:%S)T'
}

CMD() {
  local -a cmd=("$@")   # bezpečně uložený příkaz + argumenty
  local STDO STDR rc RESULT
  STDO="$(mktemp)"; STDR="$(mktemp)"

  # spustit => výstup zrcadlit na obrazovku a zároveň do dočasných souborů
  if "${cmd[@]}" > >(tee "$STDO") 2> >(tee "$STDR" >&2); then
    rc=0; RESULT='OK ✅'
  else
    rc=$?; RESULT="FAILED ❌ rc=${rc}"
  fi

  # souhrnný řádek
  paste \
    <(TD) \
    <(printf "%q " "${cmd[@]}") \
    <(printf "STDOUT\n"; cat "$STDO") \
    <(printf "STDERR\n"; cat "$STDR") \
    <(printf "# %s\n" "$RESULT")

  rm -f "$STDO" "$STDR"
  return "$rc"
}

nft_cleaning() { awk '{ gsub(/xt target "MASQUERADE"/,"masquerade");
gsub(/counter packets [0-9]+ bytes [0-9]+/,"counter");
print }'; }

# list of running containers
mapfile -t containers < <(docker ps -q)
conteiners_list="$(docke ps|awk '{print $2}')"

# stoping containers
for container in "${containers[@]}"; do
    echo "All running containers must be stopped and destroyed to obtain the firewall clean rules for the new application."
    echo "Stopping container... ${container}"
    docker ps|grep --color "${container}"|awk '{print $2}'
    CMD docker stop "${container}"
done

# decomissioning
docker system prune -f
docker compose down -v

# building
CMD sudo nft flush ruleset
echo "${DOCKER_SETTINGS_TEMP}"|sudo tee /etc/docker/daemon.json
CMD sudo systemctl restart docker
sudo nft list ruleset 2>/dev/null|nft_cleaning|tee "${NFT_DOCKER_GLOBAL}"
CMD docker compose up -d
sudo nft list ruleset 2>/dev/null|nft_cleaning|tee "${NFT_APP_TMP}"
"${FULL_PATH}/nft_diff.py" "${NFT_DOCKER_GLOBAL}" "${NFT_APP_TMP}" > "${NFT_APP_FINAL}"
rm "${NFT_APP_TMP}"

sudo mkdir -pv /etc/nftables.d/
CMD sudo cp -v "${NFT_DOCKER_GLOBAL}" /etc/nftables.d/
CMD sudo cp -v "${NFT_APP_FINAL}" "/etc/nftables.d/3_docker_${APP_NAME}.conf"
   if ! grep  '/etc/nftables.d/\*.conf' /etc/nftables.conf|grep '^include' -q; then
      echo 'include "/etc/nftables.d/*.conf"'|sudo tee -a /etc/nftables.conf
      sudo systemctl restart nftables
   fi
echo "${DOCKER_SETTINGS_NEW}"|jq -r|sudo tee /etc/docker/daemon.json

CMD echo "Now sleeping for a minute..."
CMD sleep 60

for s in $(docker compose ps -a --services); do
  docker compose logs --no-color --timestamps "$s"|tail -n500|tee -a > "${LOG_DIR}/${s}.log"
done
docker compose ps

echo "Remember to start stopped containers using docker compose up -d in their docker compose directory"
echo "${conteiners_list}"

archive="$(echo "${HOME}/${APP_NAME}_$(TD).tgz"|tr ':' '-')"
echo "Now i create a backup file with logs for chatgpt analysis: ${archive}"
cd ..
tar czpf "${archive}" "${APP_NAME}"/

exit
