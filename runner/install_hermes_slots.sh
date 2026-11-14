#!/bin/bash
# Install Hermes v0.23.1 into each agent slot's own home (its paths are baked in), run it once, and keep the
# whole resulting home as /opt/hermes-slots/agN. Every Hermes chain starts from an rsync of that snapshot.
set -u
one() {
  u=$1; h=/home/$u; p=/opt/hermes-slots/$u
  if [ -d $p/.hermes/tools ] && [ -d $p/.hermes/hermes-agent ]; then echo "$u: snapshot already complete"; return; fi
  if [ ! -d $h/.hermes/tools ]; then   # wiped (or never installed): install again
    rm -rf $p; find $h -mindepth 1 -delete
    HOME=$h bash -c 'curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash -s -- --skip-setup --branch v0.23.1' > /var/log/hermes-install-$u.log 2>&1
    rm -f $h/.hermes/.hermes-update-in-progress.lock
    chown -R $u:$u $h
    runuser -u $u -- env -i HOME=$h PATH=$h/.local/bin:/usr/bin:/bin hermes --version > /tmp/hv-$u 2>&1
  fi
  mkdir -p $p
  rsync -a --exclude=/.trip --exclude=/work --exclude=/.picoclaw $h/ $p/
  chown -R $u:$u $p; chmod 700 $p
  echo "$u: $(du -sh $p | cut -f1) $(grep -m1 -o 'Hermes Agent v[0-9.]*' /tmp/hv-$u 2>/dev/null)"
}
apt-get install -y -qq rsync >/dev/null 2>&1 || true
mkdir -p /opt/hermes-slots && chmod 711 /opt/hermes-slots
for i in 1 2 3 4 5 6 7 8; do one ag$i & sleep 3; done
wait
echo all-done
