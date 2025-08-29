#!/usr/bin/env bash
STDERR(){ cat - 1>&2; }

help1(){ { echo "usage:"; echo "${0##*/} /path/to/dir/"; } | STDERR; exit 1; }
help2(){ echo "This script requires the docker compose plugin version 2 to be installed." | STDERR; exit 2; }
help3(){ echo "The directory does not contain a docker-compose.yml file." | STDERR; exit 3; }
help4(){ echo "The docker-compose.yml file contains errors." | STDERR; exit 4; }

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

APP_NAME="$(basename "${FULL_PATH}")"
LOG_DIR="${FULL_PATH}/logs/"
LOG_FILE="${LOG_DIR}/stdout_stderr.log"
NFTABLES="${FULL_PATH}/etc/nftables.d/"
NFT_DOCKER_GLOBAL="${NFTABLES}/2_docker_global.conf"
NFT_APP_TMP="${NFTABLES}/3_docker_${APP_NAME}_tmp.conf"
NFT_APP_FINAL="${NFTABLES}/3_docker_${APP_NAME}.conf"

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


# decomissioning
docker system prune -f
docker compose down -v
docker system prune -f

# building
CMD sudo nft flush ruleset
CMD sudo systemctl daemon-reload
CMD sudo systemctl stop docker.service docker.socket
CMD sudo systemctl start docker.service docker.socket
sudo nft list ruleset 2>/dev/null|nft_cleaning|tee "${NFT_DOCKER_GLOBAL}"
CMD docker compose up -d
sudo nft list ruleset 2>/dev/null|nft_cleaning|tee "${NFT_APP_TMP}"
"${FULL_PATH}/nft_diff.py" "${NFT_DOCKER_GLOBAL}" "${NFT_APP_TMP}" > "${NFT_APP_FINAL}"
rm "${NFT_APP_TMP}"

read -erp 'Do you want to deploy nftables rules to /etc/nftables.d now? Yes or No: ' -i 'Yes' nft_deploy
if [[ ${nft_deploy} == Yes ]]; then
   sudo mkdir -pv /etc/nftables.d/
   CMD sudo cp -v "${NFT_DOCKER_GLOBAL}" /etc/nftables.d/
   CMD sudo cp -v "${NFT_APP_FINAL}" "/etc/nftables.d/3_docker_${APP_NAME}.conf"
   if ! grep  '/etc/nftables.d/\*.conf' /etc/nftables.conf|grep '^include' -q; then
      echo 'include "/etc/nftables.d/*.conf"'|sudo tee -a /etc/nftables.conf
   CMD sudo systemctl restart nftables
   fi
   echo "To remove iptables permissions for Docker, you must also modify /etc/docker/daemon.json."
fi


CMD echo "Now sleeping for a minute..."
CMD sleep 60
docker compose ps
for s in $(docker compose ps -a --services); do
  docker compose logs --no-color --timestamps "$s"|tail -n500|tee -a > "${LOG_DIR}/${s}.log"
done

archive="$(echo "${HOME}/${APP_NAME}_$(TD).tgz"|tr ':' '-')"
echo "Now i create a backup file with logs for chatgpt analysis: ${archive}"
cd ..
tar czpf "${archive}" "${APP_NAME}"/

exit
