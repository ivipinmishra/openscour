#!/bin/sh
# Starts SearXNG, sending its search engine queries through the proxies in
# SEARCH_PROXIES (comma-separated) when it is set. Proxy URLs often contain
# passwords, so they come from the environment instead of settings.yml, which
# is in git. The proxies are written into a copy of settings.yml under
# "outgoing:"; SearXNG takes them in turn for each query.
set -eu

settings=/etc/searxng/settings.yml

if [ -n "${SEARCH_PROXIES:-}" ]; then
    if ! grep -q '^outgoing:' "$settings"; then
        echo "start.sh: $settings has no 'outgoing:' section to add SEARCH_PROXIES to" >&2
        exit 1
    fi
    proxied=/tmp/settings-with-proxies.yml
    umask 077
    # Each URL becomes a single-quoted YAML string (a quote inside is doubled).
    awk -v q="'" '
        { print }
        /^outgoing:/ {
            print "  proxies:"
            print "    all://:"
            n = split(ENVIRON["SEARCH_PROXIES"], urls, ",")
            for (i = 1; i <= n; i++) {
                url = urls[i]
                gsub(/^[ \t]+|[ \t]+$/, "", url)
                if (url == "") continue
                gsub(q, q q, url)
                print "      - " q url q
            }
            # Seconds added to the engine timeouts for the extra hop.
            print "  extra_proxy_timeout: 1"
        }
    ' "$settings" > "$proxied"
    export SEARXNG_SETTINGS_PATH="$proxied"
    echo "start.sh: search engine queries go through $(echo "$SEARCH_PROXIES" | tr ',' '\n' | grep -c '[^[:space:]]') proxies"
fi

exec /usr/local/searxng/entrypoint.sh
