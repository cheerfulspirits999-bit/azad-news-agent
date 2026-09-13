#!/usr/bin/env bash
# One monitoring cycle. Safe to run from cron / systemd / GitHub Actions.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
TS=$(date +%Y%m%d-%H%M)
echo "== cycle $TS ==" | tee -a logs/monitor.log
python3 news_agent/monitor.py --hours 30 --limit 12 2>>logs/monitor.log | tee -a logs/monitor.log
# If a story JSON was approved in news_agent/outbox/ it gets packaged here.
# Actual posting still requires the Graph API credentials in config.json,
# or the pending package is handed to Metricool / manual posting.
for f in news_agent/outbox/*.json; do
  [ -e "$f" ] || continue
  echo "== packaging $f ==" | tee -a logs/monitor.log
  python3 news_agent/publish.py "$f" 2>>logs/monitor.log | tee -a logs/monitor.log
done
