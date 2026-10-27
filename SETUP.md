# Step 0 — Setup checklist

> **Status, 27 Oct:** done. Zetaris passes all 9 `verify.sh` checks (http://localhost:3000, `admin@zetaris.com`, password in `.env`). Nightshift answers with pinned Nemotron Lightning and Ultra. Still open: Java isn't installed, and the Zetaris JDBC driver needs it.

Order matters: start **A** first, because the Zetaris images are large and pull slowly on Apple Silicon (amd64 emulation). Do **B** and **C** while they download.

Never paste keys or tokens into chat or commit them. They go in Nightshift, in `zetaris/.env` (gitignored), or in a terminal prompt.

This Mac: Apple M4 Pro, 24 GB RAM. Docker Desktop 29.6 is running with 12 CPUs, ~8 GB memory, 51 GB free disk. That meets Zetaris's minimum (8 GB recommended, below 6 GB fails).

---

## A. Zetaris (data layer) — start first

**You do (account steps):**
1. Sign up at https://www.zetaris.com/cloud → **Start Free** (or https://cloud.enterprise.zetaris.com/register). Free use is a 30-day evaluation.
2. Open https://cloud.enterprise.zetaris.com/dashboard/download. Download **`zetaris-platform.zip`**, and keep the page open: it shows the registry sign-in details.
3. Move the zip to `~/Documents/amazon/zetaris-platform.zip`. That's *outside* the project folder, so the platform's `.env` never ends up in our repo.

**Then in a terminal (you type the registry password yourself):**
```bash
cd ~/Documents/amazon && unzip zetaris-platform.zip && cd zetaris-platform
```
```bash
docker login zetarishackathon.azurecr.io
```
```bash
./preflight.sh
```

**Edit `zetaris-platform/.env`** (keep the issued image settings):

| Variable | Value |
|---|---|
| `ZETARIS_ADMIN_EMAIL` | your email |
| `ZETARIS_ADMIN_PASSWORD` | 8+ chars, upper + lower + digit + one of `$ @ ! % * ? &` (no other punctuation) |
| `ZETARIS_ADMIN_ORG` | e.g. `Pavilion` (choose before first start) |
| `ZSPARK_DRIVER_MEM` | `2g` |
| `ZSPARK_DRIVER_CORES` | `2` |
| `ZSPARK_MASTER_URL` | `local[2]` |
| `ZETARIS_API_MAX_HEAP` | `1g` |
| `UI_PORT` | `3000` |
| `ALLOWED_ORIGINS` / `GUI_URL` | `http://localhost:3000` |
| `COMPOSE_PROFILES` | empty |
| `ZETARIS_SEED_TPCH` | `true` |

**Pull and start** (I can run these once the login works):
```bash
chmod 600 .env && docker compose config --quiet && docker compose pull && docker compose up -d
```

**Done when:** `./verify.sh` passes all 9 checks, and `SELECT 1 AS one;` returns `1` in the SQL Editor at http://localhost:3000. `postgres`, `driver`, `api`, `ui`, `ai-datasteward` are running; `init` and `bootstrap` show `Exited (0)`.

If the local install fights us (memory, emulation), fall back to Zetaris Cloud Gateway.

---

## B. NVIDIA API key

1. Go to https://build.nvidia.com/models and sign in (use a normal-looking email: auto-generated-looking addresses trigger extra checks).
2. Open a Nemotron model with an API endpoint, e.g. **nemotron-3.5-lightning-30b-a3b**, and click **Generate API Key**. One key works for every model in the catalog.
3. Copy the key (`nvapi-…`) somewhere safe, like your password manager. It goes **only** into Nightshift (step C4).

**If you see "Please contact support to verify your account":** DM **Matt Cool** on HackOS (Help NVIDIA Tools channel) with your build.nvidia.com email, country, a screenshot, and whether phone verification failed. Meanwhile we can continue: NVIDIA is optional for eligibility, but needed for the DGX Spark award.

---

## C. Meterless Nightshift (the app's AI provider)

1. Go to https://meterless.ai, scroll to the install cards, and choose **Mac M1+** (that's the right edition for an M4; **M5+** is for M5–M6 Macs).
2. Enter your email at the access gate, confirm the verification email, download, install, launch. Finish the first-run prompts and leave it running.
   - Use the **installed app**, not the browser version: only the app exposes the local Runtime our code calls.
3. Check you have the newest build (1.2.7 or later; several bugs were fixed on Oct 25).
4. **Setup → Providers → Cloud Providers → NVIDIA:** paste the NVIDIA key and save.
5. **NIGHT tab → LANES:**

   | Lane | Model | Job |
   |---|---|---|
   | Fast worker | `nvidia/nemotron-3.5-lightning-30b-a3b` | Planner, Investigator |
   | Deep / judge | `nvidia/nemotron-3-ultra-550b-a55b` | Critic, Reporter |

   (The official guide suggests Super VL for the deep lane, but the Discord reports it failing with HTTP 500s, and Super 120B returns 404. Ultra is the one confirmed working.)
6. Run a short test inside Nightshift and confirm the response says a Nemotron model handled it.
7. **Console** (top right): copy the **Nightshift URL** and **API key** (Runtime token). This is *not* the NVIDIA key.
8. Create `zetaris/.env` from `.env.example` and put those two values in it.

**Then I run:**
```bash
./scripts/check_nightshift.sh
```
It lists the available models and sends one real chat request through Nightshift, printing which model answered.

**Done when:** Nightshift returns a real answer and the receipt names a Nemotron model.

---

## D. Not needed in Step 0

- **Cursor:** already installed. We open the project in it when coding starts.
- **Meterless "Power Prompt"** (meterless.ai/engines.html → POWER → *Add Meterless as a provider*): we use it in Step 2, when we write the app's provider code.
- **GitHub repo:** create it when the first code lands.
