#!/usr/bin/env bash
set -euo pipefail

STACK=${STACK:-eventhive}
REGION=${REGION:-ap-south-1}
ADMIN_KEY=${ADMIN_KEY:?set ADMIN_KEY first}

if [ "${1:-}" = "destroy" ]; then
  bucket=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='SiteBucketName'].OutputValue" --output text)
  aws s3 rm "s3://$bucket" --recursive
  sam delete --stack-name "$STACK" --region "$REGION" --no-prompts
  exit 0
fi

sam build
sam deploy --stack-name "$STACK" --region "$REGION" --resolve-s3 --capabilities CAPABILITY_IAM \
  --no-confirm-changeset --parameter-overrides AdminKey="$ADMIN_KEY" NotifyEmail="${NOTIFY_EMAIL:-}"

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

api=$(out ApiUrl)
bucket=$(out SiteBucketName)
echo "window.API_BASE = \"$api\"" > /tmp/config.js
aws s3 cp index.html "s3://$bucket/index.html"
aws s3 cp /tmp/config.js "s3://$bucket/config.js"

echo "API:  $api"
echo "Site: $(out SiteUrl)"
