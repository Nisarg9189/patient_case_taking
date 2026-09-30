# Deploy on Azure Container Apps (scale to zero)

The app runs as one container on Azure Container Apps: HTTPS and WebSockets included, **0
replicas when nobody uses it** (no compute charge; the free monthly grant covers roughly 100
hours of use), at most 1 replica (an interview in progress lives in the app's memory). The
summary worker runs inside the app (`RUN_SUMMARY_WORKER=1`), so nothing else has to run.

```
GitHub (push to main) --Actions--> ghcr.io/nisarg9189/patient_case_taking:latest
                                            |
Azure Container Apps (Southeast Asia) <-----+   pulls the image
   app + summary worker, secrets from patient-nlp/.env
   -> Neon Postgres + Neon Auth, Kafka, Redis, Gemini, OpenAI (all hosted elsewhere)
```

## Once

1. **Build setting on GitHub.** Repository → Settings → Secrets and variables → Actions →
   Variables → New variable `VITE_NEON_AUTH_URL` = the value in `frontend/.env.local` (it is
   public: the browser uses it).
2. **Push to `main`.** The *container image* workflow builds the image (Actions tab, ~5 min).
3. **Make the image public**, so Azure can pull it without a password: GitHub → your profile →
   Packages → `patient_case_taking` → Package settings → Change visibility → Public. The
   image has the code (the repository is public anyway) and no keys (`.dockerignore` keeps
   every `.env` out of it).
4. **Azure CLI.** `brew install azure-cli`, then `az login` with your Azure for Students
   account.
5. **Deploy.** From the project folder: `deploy/azure/deploy.sh`. It creates the resource
   group, the Container Apps environment (without Log Analytics, which would cost) and the
   app, stores the keys from `patient-nlp/.env` as secrets, and prints the address.
6. **Neon Auth.** Neon console → the project → Auth → add the printed `https://…azurecontainerapps.io`
   address as a trusted domain. Without it, sign-in is refused.

## Updating

Push to `main` (the image is rebuilt), then run `deploy/azure/deploy.sh` again: it rolls out
the new image and any changed keys.

## Good to know

- **First visit after a quiet spell**: the app starts (and Neon wakes the database), about
  30 s. The app scales back to zero after about 5 minutes without traffic.
- **Logs**: `az containerapp logs show --name patient-intake --resource-group patient-intake --follow`
  (live log stream; no Log Analytics needed).
- **Cost**: the app itself is within the free grant for light use; each interview still
  costs Gemini/OpenAI usage (about ₹5).
- **Delete everything**: `az group delete --name patient-intake`.
