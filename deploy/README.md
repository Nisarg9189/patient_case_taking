# Deploying the interview app on AWS EC2

One server runs everything: the React page and the FastAPI backend in one Docker
container, behind Caddy for HTTPS (browsers only allow the microphone on HTTPS pages).
The load test showed one backend process handles 250+ simultaneous interviews with fake
providers, so one t3.medium is enough until the providers' own limits are reached.

## 1. Launch the server (AWS console, once)

EC2 → Launch instance:

| Setting | Value |
|---|---|
| Region | Asia Pacific (Mumbai) `ap-south-1` if the patients are in India |
| Image | Ubuntu Server 24.04 LTS (x86) |
| Instance type | `t3.medium` (2 vCPU, 4 GB) |
| Key pair | create one, download the `.pem` file (e.g. to `~/.ssh/intake.pem`, then `chmod 400`) |
| Storage | 20 GB gp3 |
| Security group | SSH (22) from **My IP** only; HTTP (80) and HTTPS (443) from anywhere |

Then EC2 → Elastic IPs → Allocate → Associate it with the instance. Without it the
public IP (and so the HTTPS address) changes every time the instance is stopped.

Check Billing → Credits that your credits cover EC2. Stop the instance when you are not
using it: only the disk and the Elastic IP are charged then (a few dollars a month).

## 2. Deploy (from your Mac, in the project root)

First time (installs Docker, copies `patient-nlp/.env` to the server as `deploy/app.env`,
chooses the address `<ip-with-dashes>.sslip.io`, builds and starts):

```bash
deploy/deploy.sh ubuntu@<elastic-ip> ~/.ssh/intake.pem --first
```

With your own domain instead (point an A record at the Elastic IP first):

```bash
DOMAIN=intake.example.com deploy/deploy.sh ubuntu@<elastic-ip> ~/.ssh/intake.pem --first
```

After code changes (copies the code, rebuilds, restarts; keys and domain stay):

```bash
deploy/deploy.sh ubuntu@<elastic-ip> ~/.ssh/intake.pem
```

The script prints the address, e.g. `https://13-233-10-20.sslip.io`. Caddy gets the
certificate on the first visit, which can take a few seconds.

## 3. Operate

```bash
ssh -i ~/.ssh/intake.pem ubuntu@<elastic-ip>
cd ~/case_taking/deploy
sudo docker compose logs -f app      # interview logs (the ⏱ timing lines too)
sudo docker compose ps               # health
sudo docker compose restart app
```

The API keys live only in `~/case_taking/deploy/app.env` on the server (mode 600); they
are never in the image. To change a key, edit that file and run
`sudo docker compose up -d app`.

## Notes

- `t3.medium` is burstable: 20 % of each vCPU is guaranteed, above that it spends CPU
  credits ("unlimited" mode bills sustained use above the baseline). Normal use and small
  real-provider load tests stay well under it.
- Kafka/Redis (JEV) are cloud services, so they work from the server too; JEV is advisory
  and the interview does not depend on it.
- Scaling out later: several copies of the same container behind an AWS load balancer
  (ECS Fargate + ALB). Each interview lives on one WebSocket, so no shared state is needed.
