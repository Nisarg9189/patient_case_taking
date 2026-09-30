#!/usr/bin/env bash
# Deploy (first time) or update the app on Azure Container Apps, scaling to zero when idle.
#
#   az login                       # once
#   deploy/azure/deploy.sh         # create everything, or roll out the latest image
#
# The image is built by GitHub Actions (.github/workflows/container-image.yml) and pulled from
# GitHub Container Registry. The API keys come from ENV_FILE (default patient-nlp/.env) and
# are stored as Container Apps secrets, never in the image; values are never printed.
# Settings (environment variables, all optional):
#   RESOURCE_GROUP  default patient-intake       LOCATION  default southeastasia (Singapore,
#   ENVIRONMENT     default patient-intake-env             next to the Neon database)
#   APP             default patient-intake       IMAGE     default ghcr.io/nisarg9189/patient_case_taking:latest
set -euo pipefail

cd "$(dirname "$0")/../.."
RESOURCE_GROUP=${RESOURCE_GROUP:-patient-intake}
LOCATION=${LOCATION:-southeastasia}
ENVIRONMENT=${ENVIRONMENT:-patient-intake-env}
APP=${APP:-patient-intake}
IMAGE=${IMAGE:-ghcr.io/nisarg9189/patient_case_taking:latest}
ENV_FILE=${ENV_FILE:-patient-nlp/.env}

command -v az >/dev/null || { echo "Install the Azure CLI first: brew install azure-cli"; exit 1; }
az account show >/dev/null 2>&1 || { echo "Sign in first: az login"; exit 1; }
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE (the API keys)"; exit 1; }

# KEY=value lines -> secrets (lower-case names) and env vars that refer to them. Only
# upper-case keys: stray lines such as "sslmode=..." are skipped.
secrets=()
env_vars=("RUN_SUMMARY_WORKER=1")  # the summary worker runs inside the app (see backend/app.py)
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  [[ "$line" =~ ^[[:space:]]*# || "$line" != *=* ]] && continue
  key="${line%%=*}"; key="${key#export }"; key="${key//[[:space:]]/}"
  value="${line#*=}"
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || continue
  if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then value="${BASH_REMATCH[1]}"; fi
  [ -n "$value" ] || continue
  name=$(echo "$key" | tr 'A-Z_' 'a-z-')
  secrets+=("$name=$value")
  env_vars+=("$key=secretref:$name")
done < "$ENV_FILE"
echo "Settings from $ENV_FILE: ${#secrets[@]} (stored as secrets)"

echo "Preparing Azure (resource providers, Container Apps extension)…"
az extension add --name containerapp --upgrade --only-show-errors >/dev/null
az provider register --namespace Microsoft.App --wait --only-show-errors
az provider register --namespace Microsoft.OperationalInsights --wait --only-show-errors

if ! az group show --name "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Creating resource group $RESOURCE_GROUP in $LOCATION…"
  az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --only-show-errors >/dev/null
fi

if ! az containerapp env show --name "$ENVIRONMENT" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Creating the Container Apps environment $ENVIRONMENT (no Log Analytics: it would cost)…"
  az containerapp env create --name "$ENVIRONMENT" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" \
    --logs-destination none --only-show-errors >/dev/null
fi

if ! az containerapp show --name "$APP" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  echo "Creating the app $APP…"
  # 0.5 CPU / 1 GiB; 0 replicas when idle (free when nobody uses it), at most 1: an interview
  # in progress lives in the app's memory, so it must stay on one replica
  az containerapp create --name "$APP" --resource-group "$RESOURCE_GROUP" --environment "$ENVIRONMENT" \
    --image "$IMAGE" --target-port 8000 --ingress external --transport auto \
    --cpu 0.5 --memory 1.0Gi --min-replicas 0 --max-replicas 1 \
    --scale-rule-name http --scale-rule-type http --scale-rule-http-concurrency 50 \
    --secrets "${secrets[@]}" --env-vars "${env_vars[@]}" \
    --only-show-errors >/dev/null
else
  echo "Updating $APP: secrets, settings and the latest image…"
  az containerapp secret set --name "$APP" --resource-group "$RESOURCE_GROUP" --secrets "${secrets[@]}" \
    --only-show-errors >/dev/null
  # a new revision suffix makes Azure pull the image again even when the tag is still :latest
  az containerapp update --name "$APP" --resource-group "$RESOURCE_GROUP" --image "$IMAGE" \
    --set-env-vars "${env_vars[@]}" --min-replicas 0 --max-replicas 1 \
    --revision-suffix "r$(date +%Y%m%d%H%M%S)" --only-show-errors >/dev/null
fi

FQDN=$(az containerapp show --name "$APP" --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv)
cat <<DONE

Deployed: https://$FQDN

Next, once:
  - Neon console -> your project -> Auth -> add https://$FQDN as a trusted domain (else sign-in is refused)
The first visit after a quiet spell starts the app (scale to zero): allow ~30 s.
Logs:  az containerapp logs show --name $APP --resource-group $RESOURCE_GROUP --follow
DONE
