# Cloud deploy (Vercel + Railway + GitHub Actions)

Nothing runs on a laptop.

| Piece | Host | Role |
| --- | --- | --- |
| Next.js app | Vercel | Upload UI, review room |
| FastAPI engine | Railway | YOLO + tracking |
| Checks / API deploy | GitHub Actions | test + `railway up` on `main` |

```
browser → Vercel frontend
              └─ POST video → Railway /api/v1/analyze
                                   └─ poll /api/v1/analyze/:jobId
```

Vercel does not run Ultralytics.

## Railway
Root directory: `backend`. Dockerfile builder.

```
ENV=production
ENABLE_CV=true
YOLO_WEIGHTS=yolov8n.pt
CV_DEVICE=cpu
ALLOWED_ORIGINS=https://YOUR-APP.vercel.app
```

## Vercel
Root directory: `frontend`.

```
NEXT_PUBLIC_API_URL=https://YOUR-SERVICE.up.railway.app
```

## GitHub
Secrets: `RAILWAY_TOKEN`, `RAILWAY_SERVICE`.
Keep the Vercel GitHub integration for the frontend.
