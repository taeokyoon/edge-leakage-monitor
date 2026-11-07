#!/usr/bin/env bash
# 4단계 브로커 준비: 자체 서명 CA·서버 인증서 + 장치별 계정 (저장소 루트에서 실행)
#   bash broker/setup.sh            → 없으면 만들고, 있으면 그대로 둔다
# 만들어지는 파일은 모두 비밀이라 git에 올리지 않는다 (.gitignore)
#   broker/certs/  ca.key·ca.crt·server.key·server.crt
#   broker/passwd  mosquitto 비밀번호 파일 (해시)
#   config/secrets.yaml  계정별 평문 비밀번호 (장치·웹 서버가 읽음, 권한 600)
set -euo pipefail
cd "$(dirname "$0")/.."
CERTS=broker/certs
mkdir -p "$CERTS"
chmod 600 broker/acl  # mosquitto가 다른 사용자도 읽을 수 있는 ACL 파일을 경고함 (git은 권한을 저장하지 않음)

if [ ! -f "$CERTS/server.crt" ]; then
  # CA: 이 프로젝트 전용 인증 기관 (장치는 ca.crt로 브로커가 진짜인지 확인한다)
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=leakmon-ca" \
    -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt"
  # 서버 인증서: 접속 주소(localhost, 이 PC/Pi 이름)를 SAN에 넣어야 호스트 이름 검사를 통과한다
  openssl req -newkey rsa:2048 -nodes -subj "/CN=leakmon-broker" \
    -keyout "$CERTS/server.key" -out "$CERTS/server.csr"
  printf "subjectAltName=DNS:localhost,DNS:%s,IP:127.0.0.1\n" "$(hostname)" > "$CERTS/san.ext"
  openssl x509 -req -in "$CERTS/server.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
    -days 825 -extfile "$CERTS/san.ext" -out "$CERTS/server.crt"
  rm "$CERTS/server.csr" "$CERTS/san.ext"
  chmod 600 "$CERTS"/*.key
  echo "인증서 생성: $CERTS"
fi

if [ ! -f config/secrets.yaml ]; then
  # 계정: devices.yaml의 장치마다 1개 + 웹 서버(web). 비밀번호는 무작위
  users=$(.venv/bin/python -c "from leakmon.config import load_devices; print(' '.join([*load_devices(), 'web']))")
  rm -f broker/passwd && touch broker/passwd && chmod 600 broker/passwd
  umask 077
  echo "# setup.sh가 만든 MQTT 계정 비밀번호 (git 제외)" > config/secrets.yaml
  for user in $users; do
    password=$(openssl rand -hex 16)
    mosquitto_passwd -b broker/passwd "$user" "$password"
    echo "$user: $password" >> config/secrets.yaml
  done
  echo "계정 생성: $users"
fi
