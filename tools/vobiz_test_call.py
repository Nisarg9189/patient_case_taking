"""Phone yourself with the voice agent, to test it from a Vobiz trial account (trials cannot receive calls).

    export VOBIZ_AUTH_ID=...        # Vobiz console -> your Auth ID
    export VOBIZ_AUTH_TOKEN=...     # Vobiz console -> your Auth Token (never commit or paste it anywhere)
    export PHONE_AGENT_KEY=...      # the same secret the server has
    python3 tools/vobiz_test_call.py --app https://<your-app-host> --from <vobiz caller id> --to <your mobile>

Numbers in international form without a plus, e.g. 919876543210. Vobiz calls --to; when you answer, the
voice agent talks to you. A trial account may only be allowed to call numbers you verified in the console.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--app", required=True, help="the server's address, e.g. https://my-app.azurecontainerapps.io")
parser.add_argument("--from", dest="caller_id", required=True, help="the caller id Vobiz gives your account")
parser.add_argument("--to", required=True, help="your own mobile number")
args = parser.parse_args()

try:
    auth_id, token, key = os.environ["VOBIZ_AUTH_ID"], os.environ["VOBIZ_AUTH_TOKEN"], os.environ["PHONE_AGENT_KEY"]
except KeyError as missing:
    sys.exit(f"Set {missing} first (see the top of this file).")

answer_url = f"{args.app.rstrip('/')}/vobiz/answer?key={urllib.parse.quote(key)}"
request = urllib.request.Request(
    f"https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/",
    data=json.dumps({"from": args.caller_id, "to": args.to, "answer_url": answer_url, "answer_method": "POST",
                     "time_limit": 900}).encode(),
    headers={"X-Auth-ID": auth_id, "X-Auth-Token": token, "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print(response.status, response.read().decode())
except urllib.error.HTTPError as e:
    sys.exit(f"Vobiz refused the call: {e.code} {e.read().decode()[:400]}")
