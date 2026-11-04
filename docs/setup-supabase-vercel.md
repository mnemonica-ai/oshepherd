# Set up oshepherd with Supabase and Vercel

> **Experimental.** This needs the `0.0.25a1` candidate, which is not on PyPI yet.
> Closing a streaming client does not yet reliably cancel the inference behind it.

This is the [Supabase setup](setup-supabase.md) with one difference: the API
endpoint your clients call runs on [Vercel](https://vercel.com) as a FastAPI app,
instead of a Supabase Edge Function. Supabase still holds the queue, worker logins
and results, and Ollama still stays on your machines.

```text
Ollama client ──HTTPS──> Vercel function ──> Supabase database
                                                   ↑ outbound
                           oshepherd worker ───────┘ ──> local Ollama
```

Choose Vercel if you want larger responses: up to 4,000,000 bytes, against 1 MiB
on Edge. Both have the same 135-second request budget. Otherwise the Edge setup has
fewer moving parts.

## What you need

Everything from the [Supabase requirements](setup-supabase.md#what-you-need), and:

- A [Vercel](https://vercel.com) account on the Hobby plan.
- A Vercel access token from **Account Settings → Tokens**
  ([vercel.com/account/settings/tokens](https://vercel.com/account/settings/tokens)).
- oshepherd installed **from the wheel**, not as an editable checkout. The Vercel
  deployment uploads the exact installed package. See
  [Install the 0.0.25a1 candidate](install.md#install-the-0025a1-candidate).

No Vercel CLI is needed.

## 1. Share this machine

```sh
oshepherd share --gateway vercel
```

The Supabase steps are the same as in
[Share this machine](setup-supabase.md#1-share-this-machine): pick a model, paste
the Supabase token, choose or create a project, confirm Free, and authorize setup.
Then:

1. **Paste your Vercel token** at the hidden prompt.
2. **Pick a Hobby account or team.** oshepherd only offers verified Hobby scopes.
3. **Create or select the Vercel project.** Review the destination before you
   confirm.
4. **Review the deployment.** oshepherd stores the gateway's settings as encrypted
   Production environment variables, then deploys. It leaves unrelated
   deployments and settings alone, and it refuses a project that another
   installation already owns.
5. **Wait for "Inference verified".** The endpoint it shows is your Vercel URL,
   with the private client file next to it, as in the Edge setup.

The gateway choice is saved with the installation, so later runs of
`oshepherd share` reuse it. To switch an existing setup between Edge and Vercel,
set up again with a new `--state-dir`.

## 2. Send a request, add machines, run in the background

These work exactly as in the Supabase guide, with your Vercel URL as the
`gateway_url` in `client.json`:

- [Send a request](setup-supabase.md#2-send-a-request)
- [Connect another machine](setup-supabase.md#3-connect-another-machine)
- [Run in the background](setup-supabase.md#run-in-the-background)

Keep `share`, or its background service, running on the first machine. Vercel
serves only the API; stored-result cleanup still runs there.

## Things to know about Vercel

- **Production only.** The settings go to the Production environment. Preview
  deployments refuse inference by default, so a preview build cannot reach your
  workers by accident.
- **Deployment protection.** If Vercel shows a login page instead of the API, your
  project has deployment protection on. That page is not a working gateway; turn
  off deployment protection for this project.
- **Limits.** Vercel Hobby functions stop after 150 seconds; oshepherd keeps its
  own 135-second budget below that, and caps responses at 4,000,000 bytes. Hobby
  usage limits apply.

## Stop, disable and remove

Pausing and disabling work as in the
[Supabase guide](setup-supabase.md#stop-disable-and-remove). To remove everything,
disable the installation, then delete the Vercel project and the Supabase project
in their dashboards.

## Status

Vercel project creation through the API has been tested. A full hosted run,
deployment and inference on a new Vercel project backed by a new Supabase project,
is still open. The [progress ledger](plans/supabase/progress.md) has the evidence.
