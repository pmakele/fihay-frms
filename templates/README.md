# FiHay Farm Record Management System — v2.0 Hosted Pilot

FiHay FRMS v2.0 is the hosted, multi-farm pilot architecture. It keeps the farm-management features from v1.0 and adds secure logins, separate farm workspaces, role-based access, PostgreSQL support, persistent hosted uploads, and a PWA/offline queue for field use.

## What is included

- Farmer registration and login
- Separate farm workspaces with tenant isolation
- Roles: Owner, Manager, Field Officer, Viewer
- Optional platform administrator for pilot support
- Farm and plot records
- Crop cycles with duration and automatic expected harvest date
- Weekly crop updates
- Fertiliser and pesticide applications
- Expenses linked to crop cycles
- Receipt/photo/PDF uploads
- Harvests and produce stock
- Crop sales that reduce produce stock
- Livestock groups and individual animals
- Pregnancy, mating, birth, vaccination, deworming, sickness, treatment, recovery, death and sale events
- Livestock sales that reduce herd quantities
- Water sources
- Farm inventory for durable tools and consumables
- Incidents, attachments, lessons learned and knowledge base
- Local farm-record AI Help/search
- Master data administration
- Farm user administration
- Farm-only backup export
- Mobile-responsive PWA
- Offline text-form queue tied to the correct user and farm account

## Database

Production hosting uses `DATABASE_URL` and is intended for PostgreSQL. If `DATABASE_URL` is not set, FiHay uses a local SQLite database under `DATA_DIR` for development/testing.

## File storage

Receipt and incident files are stored under `DATA_DIR/uploads`. On a hosted service this path must be persistent. The included Render blueprint mounts a persistent disk at `/var/data`.

## Important offline behaviour

The hosted application is online-first with offline field support:

1. When connected, visited screens are cached on the farmer's device.
2. If connectivity drops while a farmer is using a cached screen, text-only form submissions can be queued in IndexedDB.
3. When connectivity returns, the queued record is sent only if the same user is signed into the same farm workspace.
4. Files such as receipt photos are not queued offline. Save the text record first and attach the file after reconnecting.

This prevents an offline record from one farmer silently syncing into another farmer's account on a shared device.

## Local test run

1. Install Python 3.11+.
2. Run `python -m pip install -r requirements.txt`.
3. Run `python -m uvicorn app:app --host 127.0.0.1 --port 8000`.
4. Open `http://127.0.0.1:8000`.
5. Register a test farm.

Local development defaults to SQLite and a development session secret. Do not use those defaults on the public internet.

## Production environment variables

- `DATABASE_URL` — PostgreSQL connection string
- `SECRET_KEY` — strong random session-signing secret
- `COOKIE_SECURE=1` — use secure HTTPS cookies
- `ALLOW_REGISTRATION=1` — allow farmer self-registration; set to `0` after pilot accounts are created if desired
- `DATA_DIR=/var/data` — persistent data/upload directory
- `ADMIN_EMAIL` — optional platform administrator email
- `ADMIN_PASSWORD` — optional platform administrator password
- `ADMIN_NAME` — optional platform administrator display name

## Pilot access model

A self-registering farmer becomes the Owner of their farm workspace. Owners and Managers can manage master data and farm users. Field Officers can enter operational records. Viewers can read records but cannot submit changes. A platform administrator can switch into pilot farms for support.

## Deployment

See `DEPLOY_RENDER.md` for the included Render deployment path. The app also includes a Dockerfile and can be deployed to other Docker-capable platforms with PostgreSQL and persistent file storage.

## Pilot limitation

This is suitable for a small, controlled farmer pilot. Before a large BAM-wide rollout, add production-grade migration tooling, email verification/password reset, formal audit logging, automated restore testing, privacy/consent policy, and a clear policy on which aggregated farm data BAM can see.
