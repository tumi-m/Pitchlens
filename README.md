# Pitchlens MVP: The Codex of Creation

Pitchlens is an end-to-end MVP web application architected as a symphony of minimalist elegance and computational precision. It democratizes advanced soccer analytics for five-a-side enthusiasts. 

## Philosophy

Pitchlens draws from Zaha Hadid's fluid geometries and Jony Ive's intuitive interfaces to provide a seamless user experience. Under the hood, the system borrows from Nick Szabo's unbreakable rigor and Grigori Perelman's topolological intelligence. 

The monolithic processing of previous iterations has been ruthlessly decoupled. The current MVP uses a decoupled approach:
- **Frontend**: A lithe Next.js 14 App Router application deployed for instant edge performance, styled using Tailwind CSS v3 with sleek, monochromatic palettes and neon accents.
- **Backend (Engine)**: A Python FastAPI microservice that extracts frames and coordinates the heavy-lifting of video ingestion. 

> **Note on Dummy Processing Pipeline:**
> As requested for this MVP phase, the video parsing pipeline uses robust "dummy data" generation to bypass heavy GPU processing requirements temporarily. This mimics the orchestration flow of the TARA app perfectly—extracting frames, pausing to mimic AI inferences, and creating deterministic statistical dashboards (xG, heatmaps, Voronoi control graphs) without hardware hanging constraints.
>
> **Future Transition to Real Roboflow Pipeline:** 
> When you are ready to pivot to the full AI:
> - **What is Needed**: A Roboflow API key (for the `soccer-players-5fuqs` object tracking model) and a deployed GPU cloud instance (RunPod RTX 4090 or Google Cloud Run w/ L4 GPUs).
> - **Estimated Costs**: The inference latency per frame using YOLOv8 via ByteTrack is low (cents per match computation), while standard API tier for Roboflow begins near $249/m. RunPod inference ranges from $0.40/hr. 

## Architectural Setup & Deployment

### 1. The Frontend (Next.js)
The interface employs Framer Motion for micro-animations and Recharts/D3 for insightful data visualization.

```bash
cd frontend
npm install
npm run dev
```

### 2. The AI Backend (FastAPI)
A standalone Python analytics engine. In production, this ingests Firebase Function webhooks.

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

### 3. Firebase Orchestration (Schema Setup)
Currently, frontend and backend simulate network requests over localhost. To link real Firebase Auth and Storage:
1. `firebase init` the project in the root.
2. Setup the `users`, `teams`, and `matches` Firestore rules in `firebase/firestore.rules`.
3. Provide `.env.local` credentials.

## Testing the MVP
1. Run both the `frontend` and `backend` development servers.
2. Open `http://localhost:3000`.
3. Drag and drop any `.mp4` video.
4. Watch the progress radial simulate AI orchestration.
5. Explore the generated Sofascore-style match dashboard.
6. Export the jsPDF analytical narrative report.

## Horizons (v2)
- Real-time WebRTC live steaming overlays.
- Monetization layers via Stripe checkouts.
- Social gamification: leaderboards and friend duels.
