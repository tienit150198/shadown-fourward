#!/bin/sh
# Run as root on 103.195.238.178 after Cloudflare's A record is in place.
set -eu
cd "$(dirname "$0")"
domain=hackathon.trioxstudios.com
expected_ip=103.195.238.178
resolved=$(dig @1.1.1.1 +short "$domain" A | sort -u)
if [ "$resolved" != "$expected_ip" ]; then
    echo "Waiting for DNS: $domain must resolve to $expected_ip (DNS only)." >&2
    exit 1
fi
test -f nginx-hackathon.conf
curl --fail --silent --show-error http://127.0.0.1:18080/api/health >/dev/null
install -d -m 755 /var/www/shadow-acme/.well-known/acme-challenge
certbot certonly --non-interactive --webroot -w /var/www/shadow-acme \
    -d "$domain" --cert-name "$domain" --keep-until-expiring \
    --deploy-hook 'nginx -t && systemctl reload nginx'

site=/etc/nginx/sites-available/shadow-hackathon
backup=$(mktemp /opt/shadow/nginx-before-https.XXXXXX)
cp -p "$site" "$backup"
install -m 644 nginx-hackathon.conf "$site"
if ! nginx -t; then
    cp -p "$backup" "$site"
    echo "Nginx validation failed; restored the previous config." >&2
    exit 1
fi
systemctl reload nginx
echo "HTTPS configured for https://$domain. Verify /api/health after Nginx reload."
