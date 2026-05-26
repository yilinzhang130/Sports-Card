# eBay Marketplace Account Deletion Webhook

This subdirectory exists **only** to host a tiny serverless webhook on
Vercel. eBay requires every production app to expose a publicly
accessible endpoint that handles user-data deletion notifications
before they will activate the Production keyset.

## Vercel project setup

In the Vercel dashboard, configure the project with:

- **Root Directory:** `webhook`
- **Framework Preset:** Other / None
- **Environment Variables:**
  - `EBAY_VERIFICATION_TOKEN` — a 32-80 character random string you
    generate yourself (e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`).
    Must match the value in the eBay developer console.

After deploy, the live URL becomes:

```
https://<your-vercel>.vercel.app/api/ebay_webhook
```

Set this URL plus the same verification token in the eBay developer
console under **Alerts and Notifications → Marketplace Account
Deletion**.

## Why a separate subdirectory?

The main `sportscards-quant` project at the repo root has 50+ heavy
ML dependencies (xgboost, catboost, ...). If Vercel sees that
`pyproject.toml`, it would try to install them all during the
serverless build and either time out or fail. Putting the webhook in
its own subdirectory with its own minimal `pyproject.toml` isolates
the build.
