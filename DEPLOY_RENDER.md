# Deploy FiHay FRMS v2.0 to Render

The repository includes `render.yaml`, `Dockerfile`, and a `/health` endpoint.

## Recommended pilot deployment

The included Blueprint creates:

- one Docker web service;
- one managed PostgreSQL database;
- one 1 GB persistent disk for uploaded receipts/incidents;
- HTTPS session cookies;
- a generated `SECRET_KEY`;
- self-registration enabled for the pilot.

## Steps

1. Put this folder into a GitHub repository.
2. In Render, create a new Blueprint and connect the GitHub repository.
3. Render reads `render.yaml` and proposes the web service, Postgres database and persistent disk.
4. Review the costs/resources before applying the Blueprint.
5. Deploy.
6. Open the generated `onrender.com` address.
7. Register the first farm account.
8. Add 2–4 pilot farmers.
9. After pilot accounts exist, consider changing `ALLOW_REGISTRATION` to `0` so only invited/administrator-created accounts are used.

## Optional platform administrator

In the web service environment, add:

- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `ADMIN_NAME`

On the next app start, that email is created/promoted as a platform administrator. Use a strong unique password and change it through the application after first login.

## Custom domain

A custom domain such as `records.fihay.co.bw` can be connected later. Do this after the pilot URL is stable.

## Uploaded files

The app stores receipts and incident attachments under `/var/data/uploads` on the persistent disk. Do not remove the persistent disk unless those files have been backed up elsewhere.

## Backups

- PostgreSQL backups are handled at the hosting/database level according to the selected plan.
- FiHay also provides a Farm Backup download under Admin → Farm Profile. It exports the current farm's records as JSON plus that farm's uploaded files.

## Scaling note

The included file-upload design uses one persistent disk and one web-service instance. That is appropriate for a small pilot. For a large rollout or multiple app instances, move uploads to object storage such as S3-compatible storage.
