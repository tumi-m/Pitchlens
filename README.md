# Pitchlens

**Unveil the Geometry of Your Game.**

Pitchlens is an end-to-end soccer analytics platform for five-a-side enthusiasts. Upload match footage, receive professional-grade analytics—Expected Goals (xG), Voronoi space control, possession chains, heatmaps, and pass networks—in under 60 seconds.

---

## Table of Contents

1. [Philosophy](#philosophy)
2. [Architecture Overview](#architecture-overview)
3. [Repository Structure](#repository-structure)
4. [Prerequisites](#prerequisites)
5. [Setup — Frontend](#setup--frontend-nextjs-14)
6. [Setup — Firebase](#setup--firebase)
7. [Setup — Python Backend](#setup--python-backend-fastapi)
8. [Environment Variables Reference](#environment-variables-reference)
9. [Deployment](#deployment)
10. [Testing](#testing)
11. [Scaling Notes](#scaling-notes)
12. [Enhancement Horizon (v2)](#enhancement-horizon-v2)

---

## Philosophy

> *"Every pass, pivot, and pressure reveals the hidden mathematics of the game."*

Pitchlens is designed around three principles:

**1. Ruthless Decoupling** — The prior monolithic architecture failed under load. Here, responsibilities are cleanly separated: a lithe Next.js 14 frontend handles ingress and egress; Firebase orchestrates state and events; a standalone Python FastAPI engine handles AI computation. Each can scale, fail, and deploy independently.

**2. Yamamoto Minimalism** — No superfluous flourishes. Deep indigos, crisp whites, verdant pitch greens. The UI guides the eye intuitively, like a perfectly weighted through-ball. Every animation serves a functional purpose: upload progress radials pulse like heartbeats; trivia loaders acknowledge user patience with playful insight.

**3. Szabo-Inspired Auditability** — Every match action is immutably logged. Firestore rules enforce least-privilege access. Signed URLs expire in 60 minutes. No client can alter match status or analytics — only the Python engine's admin SDK can write stats.

---

## Architecture Overview

```
User Browser (Next.js 14 on Vercel)
        |                      |
  Auth / Upload         Real-time Firestore
        |                      |
Firebase Auth         Firestore DB
Firebase Storage      users/teams/matches/audit
Cloud Functions               ^
        |                     |
   Storage trigger      Admin SDK write
   POST /process-match        |
        |                     |
Python FastAPI Engine (RunPod / Cloud Run GPU)
  1. Download MP4              5. Homography → 42x25m
  2. Probe metadata (FFprobe)  6. Analytics: xG, possession,
  3. YOLOv8 detection             passes, heatmaps, Voronoi
  4. ByteTrack tracking        7. Write → Firestore
```

### Lifecycle

| Step | Component | Action |
|------|-----------|--------|
| 1 | Next.js | User uploads MP4 to Firebase Storage via drag-and-drop |
| 2 | Cloud Function | `onVideoUpload` triggers: validates, mints signed URL, POSTs to Python API |
| 3 | Python Engine | Downloads video, runs full Roboflow pipeline, pushes analytics to Firestore |
| 4 | Next.js | Subscribes to Firestore real-time updates, renders live dashboard |
| 5 | Next.js | User exports one-click PDF report with narrative prose |

---

## Repository Structure

```
Pitchlens/
├── frontend/                        # Next.js 14 (App Router)
│   ├── app/
│   │   ├── layout.tsx               # Root layout — AuthProvider, ThemeProvider
│   │   ├── page.tsx                 # Home — hero, features, CTA
│   │   ├── upload/page.tsx          # Upload — dropzone, team config, progress
│   │   ├── dashboard/
│   │   │   ├── page.tsx             # Match list
│   │   │   └── [matchId]/page.tsx   # Live analytics dashboard
│   │   └── report/[matchId]/page.tsx # PDF-exportable report
│   ├── components/
│   │   ├── auth/                    # AuthModal, AuthProvider
│   │   ├── charts/                  # Recharts wrappers (possession, shots, momentum)
│   │   ├── pitch/                   # D3.js SVG pitch (heatmap, Voronoi, pass network)
│   │   └── ui/                      # Navbar, ThemeProvider
│   ├── lib/
│   │   ├── firebase/                # config, auth, firestore, storage
│   │   ├── hooks/                   # useAuth, useMatch, useUserMatches
│   │   ├── types.ts                 # Shared TypeScript types
│   │   └── utils/                   # cn, analytics helpers, trivia
│   ├── tailwind.config.ts           # Yamamoto palette — indigo, green, white
│   ├── .env.example
│   └── package.json
│
├── firebase/                        # Firebase project config
│   ├── firestore.rules              # Least-privilege Firestore security rules
│   ├── storage.rules                # MP4 upload rules (500MB, authenticated)
│   ├── firestore.indexes.json       # Composite indexes for queries
│   ├── firebase.json                # Hosting, functions, emulator config
│   ├── .firebaserc                  # Project alias
│   └── functions/
│       ├── src/index.ts             # onVideoUpload, onMatchComplete, deleteMatch
│       ├── package.json
│       └── tsconfig.json
│
├── backend/                         # Python FastAPI AI Engine
│   ├── app/
│   │   ├── main.py                  # FastAPI app factory, CORS, lifespan
│   │   ├── routers/matches.py       # POST /process-match, GET /health
│   │   ├── models/match.py          # Pydantic models (request, analytics)
│   │   └── services/
│   │       ├── pipeline.py          # Full Roboflow pipeline (7 stages)
│   │       └── firestore_client.py  # Firebase Admin SDK writes
│   ├── tests/
│   │   └── test_pipeline.py         # xG, heatmap, clustering unit tests
│   ├── Dockerfile                   # Multi-stage (CPU + GPU targets)
│   ├── docker-compose.yml           # Local dev orchestration
│   ├── pyproject.toml               # Poetry dependency management
│   └── .env.example
│
└── README.md
```

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Node.js | >= 20 | Frontend & Functions |
| npm | >= 9 | Package manager |
| Python | >= 3.11 | Backend engine |
| Poetry | >= 1.8 | Python dependency management |
| Firebase CLI | >= 13 | Firebase deployment |
| Docker | >= 24 | Backend containerisation |
| FFmpeg | >= 6 | Video metadata probing |

Optional for GPU inference:
- CUDA Toolkit >= 12.1
- nvidia-container-toolkit (for Docker GPU)

---

## Setup — Frontend (Next.js 14)

```bash
cd frontend

# Install dependencies
npm install

# Copy environment file
cp .env.example .env.local
# Fill in all NEXT_PUBLIC_FIREBASE_* values from Firebase Console

# Start dev server
npm run dev
# Open http://localhost:3000
```

### Configuring Firebase SDK keys

1. Go to Firebase Console → Your Project → Project Settings → General
2. Under "Your apps", click "Web app" → copy the firebaseConfig object
3. Map each field to the corresponding NEXT_PUBLIC_FIREBASE_* variable in .env.local

---

## Setup — Firebase

```bash
cd firebase

# Install Firebase CLI globally (if not already)
npm install -g firebase-tools

# Login
firebase login

# Set your project
firebase use --add
# Select your project, alias: default

# Install Functions dependencies
cd functions && npm install && cd ..

# Deploy everything
firebase deploy --only firestore:rules,storage,functions

# Or deploy incrementally:
firebase deploy --only firestore:rules
firebase deploy --only storage
firebase deploy --only functions
```

### Enable Firebase Services

In the Firebase Console, enable:
- **Authentication**: Email/Password + Google provider
- **Firestore Database**: Start in production mode (rules will be deployed)
- **Storage**: Default bucket
- **Functions**: Requires Blaze (pay-as-you-go) plan

### Cloud Functions Environment Variables

```bash
# Set secrets (never commit these)
firebase functions:secrets:set API_SECRET_KEY
firebase functions:secrets:set PYTHON_API_URL
firebase functions:secrets:set APP_URL
```

### Running with Emulators (Local Development)

```bash
firebase emulators:start
# Emulator UI: http://localhost:4000
# Firestore:   http://localhost:8080
# Auth:        http://localhost:9099
# Storage:     http://localhost:9199
# Functions:   http://localhost:5001
```

---

## Setup — Python Backend (FastAPI)

```bash
cd backend

# Install Poetry (if not installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Copy environment file
cp .env.example .env
# Fill in ROBOFLOW_API_KEY, FIREBASE_PROJECT_ID, API_SECRET_KEY, etc.

# Place your Firebase service account JSON
# Download from Firebase Console -> Project Settings -> Service Accounts -> Generate new private key
cp ~/Downloads/your-service-account.json ./service-account.json

# Run locally
poetry run uvicorn app.main:app --reload --port 8080
# API docs: http://localhost:8080/docs
```

### Roboflow Model Setup

The pipeline uses Roboflow's hosted YOLOv8 football detection model:

- Model: `football-players-detection-3zvbc` / version 9
- Detects: players, ball, goalkeepers, referees

1. Sign up at roboflow.com
2. Go to Settings → API Keys and copy your key
3. Set `ROBOFLOW_API_KEY` in `.env`

You can substitute your own custom-trained model by updating `ROBOFLOW_PROJECT` and `ROBOFLOW_VERSION`.

### Running with Docker (Local)

```bash
cd backend

# Build image
docker build --target final -t pitchlens-api .

# Run with env file
docker run --env-file .env \
  -v $(pwd)/service-account.json:/app/service-account.json:ro \
  -p 8080:8080 \
  pitchlens-api

# Or use compose
docker compose up
```

### Testing the API locally

```bash
curl -X POST http://localhost:8080/api/v1/process-match \
  -H "Authorization: Bearer YOUR_API_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "matchId": "test-match-001",
    "videoUrl": "https://your-signed-url.mp4",
    "teamColors": {"home": "#FF0000", "away": "#0000FF"}
  }'
```

---

## Environment Variables Reference

### Frontend (frontend/.env.local)

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_FIREBASE_API_KEY` | Firebase Web API key |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | `<project>.firebaseapp.com` |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | Firebase project ID |
| `NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET` | `<project>.appspot.com` |
| `NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID` | FCM sender ID |
| `NEXT_PUBLIC_FIREBASE_APP_ID` | Firebase app ID |
| `NEXT_PUBLIC_API_URL` | Python backend URL |

### Firebase Functions (set via firebase functions:secrets:set)

| Secret | Description |
|--------|-------------|
| `API_SECRET_KEY` | Shared secret for server-to-server auth |
| `PYTHON_API_URL` | Deployed Python API URL |
| `APP_URL` | Frontend URL (for FCM deep links) |

### Backend (backend/.env)

| Variable | Description |
|----------|-------------|
| `API_SECRET_KEY` | Must match Functions secret |
| `ROBOFLOW_API_KEY` | Roboflow API key |
| `ROBOFLOW_WORKSPACE` | Roboflow workspace slug |
| `ROBOFLOW_PROJECT` | Roboflow project slug |
| `ROBOFLOW_VERSION` | Model version number |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account JSON |
| `FIREBASE_PROJECT_ID` | Firebase project ID |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `FRAME_SUBSAMPLE` | Process every Nth frame (default: 5) |
| `PIPELINE_WORKERS` | Thread pool size (default: 2) |

---

## Deployment

### Frontend → Vercel

```bash
cd frontend

# Install Vercel CLI
npm i -g vercel

# Deploy
vercel --prod

# Set environment variables in Vercel dashboard:
# Project -> Settings -> Environment Variables -> Add all NEXT_PUBLIC_* vars
```

### Backend → Google Cloud Run (GPU)

```bash
cd backend

# Build and push to Artifact Registry
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/pitchlens-api

# Deploy with GPU
gcloud run deploy pitchlens-api \
  --image gcr.io/YOUR_PROJECT_ID/pitchlens-api \
  --platform managed \
  --region us-central1 \
  --gpu=1 \
  --gpu-type=nvidia-l4 \
  --memory=8Gi \
  --cpu=4 \
  --concurrency=1 \
  --timeout=600 \
  --set-env-vars="ENV=production,FIREBASE_PROJECT_ID=YOUR_PROJECT_ID" \
  --set-secrets="API_SECRET_KEY=API_SECRET_KEY:latest,ROBOFLOW_API_KEY=ROBOFLOW_API_KEY:latest" \
  --allow-unauthenticated
```

### Backend → RunPod (Alternative GPU)

1. Go to runpod.io → Deploy → Custom Pod
2. Select GPU template: RTX 4090 or A100
3. Set container image: your Docker Hub or Artifact Registry image
4. Set environment variables in the RunPod dashboard
5. Expose port 8080
6. Note the pod URL and set `PYTHON_API_URL` in Firebase Functions secrets

---

## Testing

### Frontend

```bash
cd frontend
npm run type-check  # TypeScript check
npm run lint        # ESLint
```

### Backend Unit Tests

```bash
cd backend
poetry run pytest tests/ -v
```

### Integration Test — Full Pipeline

1. Deploy Firebase emulators: `firebase emulators:start`
2. Run Python backend: `poetry run uvicorn app.main:app --reload`
3. Upload a sample five-a-side video from Roboflow Universe:
   - Search for "football" datasets at universe.roboflow.com
   - Download a sample video clip
4. Watch Firestore update in real-time via emulator UI at http://localhost:4000

---

## Scaling Notes

### Handling High Video Volume

For more than 10 concurrent matches, replace the thread pool with Celery + Redis:

```python
# backend/app/workers/celery_app.py
from celery import Celery
app = Celery("pitchlens", broker="redis://redis:6379/0")

@app.task
def process_match_task(match_id, video_url, team_colors=None):
    pipeline = MatchPipeline()
    analytics = pipeline.run(match_id, video_url, team_colors)
    write_match_analytics(match_id, analytics)
```

### Frame Subsampling Tuning

| FRAME_SUBSAMPLE | Processing speed | Accuracy |
|-----------------|------------------|----------|
| 3 | Slower | Highest |
| 5 (default) | ~2x real-time on GPU | Good |
| 10 | ~5x real-time | Reduced (misses fast events) |

### Firestore Read Costs

With real-time dashboard subscriptions per match, cost per active match is approximately 1 read per update. Use `onSnapshot` unsubscribe in `useEffect` cleanup to avoid orphaned listeners.

---

## Enhancement Horizon (v2)

| Feature | Implementation Sketch |
|---------|----------------------|
| **Live Streaming** | WebRTC ingress → frame buffer → PyAV → pipeline (WebSocket back to frontend) |
| **Multi-angle Sync** | Upload multiple videos with timestamp metadata; sync via cross-correlation of audio peaks |
| **RTSP Club Cameras** | RTSP → GStreamer pipeline → frame queue → existing pipeline |
| **Social Leaderboards** | Firestore `leaderboards/{sport}/{period}` collection; Cloud Scheduler to aggregate weekly |
| **Stripe Monetisation** | `stripe.webhooks` → Cloud Function → update `users/{uid}.plan` custom claim; gate features on plan |
| **Multi-sport** | Swap Roboflow model slug (e.g., cricket-ball-detection); adjust analytics rules |
| **Narrative AI** | Replace rule-based narrative with Gemini 1.5 Flash: "Summarise this match: {stats_json}" |

---

## Freemium Tier Design

| Tier | Price | Matches/month | Features |
|------|-------|---------------|---------|
| **Free** | $0 | 3 | Basic heatmaps, possession, score |
| **Analyst** | $12/mo | Unlimited | xG, Voronoi, pass network, PDF export |
| **Club** | $99/mo | Unlimited | Multi-angle, API access, RTSP, leaderboards |

---

## Security Notes

- **Signed URLs expire** in 60 minutes — Python engine must process within that window
- **Firestore rules** prevent clients from writing status changes or analytics — only Cloud Functions (admin SDK) can
- **API key** is passed only server-to-server (Cloud Function → Python); never exposed to the browser
- **Storage rules** enforce userId path matching — users can only write to their own bucket prefix
- **Audit log** collection is append-only from the client and admin-read-only

---

*Built with Next.js 14, Firebase, Roboflow, supervision, and an obsession with the hidden mathematics of five-a-side football.*
