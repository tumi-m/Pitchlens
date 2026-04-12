from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
import uvicorn
import uuid
import os
import tempfile
from pipeline.video_processor import process_video, mock_firestore

UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "pitchlens_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="Pitchlens MVP AI Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/process-match")
async def process_match(background_tasks: BackgroundTasks, video: UploadFile = File(...)):
    match_id = str(uuid.uuid4())
    
    # Save uploaded file to disk
    file_path = os.path.join(UPLOAD_DIR, f"{match_id}.mp4")
    contents = await video.read()
    with open(file_path, "wb") as f:
        f.write(contents)
    
    mock_firestore[match_id] = {
        "status": "queued",
        "progress": 0,
        "message": "Video uploaded. Queued for CV processing."
    }
    background_tasks.add_task(process_video, match_id, file_path)
    return {"status": "queued", "matchId": match_id, "message": "Background task started."}

@app.get("/match/{match_id}")
async def get_match_status(match_id: str):
    data = mock_firestore.get(match_id)
    if not data:
        raise HTTPException(status_code=404, detail="Match not found")
    return data

@app.get("/match/{match_id}/highlights")
async def stream_highlights(match_id: str, request: Request):
    """
    Stream the highlights MP4 with HTTP range request support.
    Range requests are required for HTML5 <video> seeking to work.
    """
    data = mock_firestore.get(match_id)
    if not data:
        raise HTTPException(status_code=404, detail="Match not found")
    if not data.get("highlightsReady"):
        raise HTTPException(status_code=404, detail="Highlights not ready or no key events detected")
    path = data.get("highlightsPath")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Highlights file missing")

    file_size = os.path.getsize(path)
    range_header = request.headers.get("range")

    if range_header:
        # Parse "bytes=start-end"
        byte_range = range_header.replace("bytes=", "").split("-")
        start = int(byte_range[0])
        end   = int(byte_range[1]) if byte_range[1] else file_size - 1
        end   = min(end, file_size - 1)
        length = end - start + 1

        def iter_file(s, e):
            with open(path, "rb") as f:
                f.seek(s)
                remaining = e - s + 1
                while remaining:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            iter_file(start, end),
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range":  f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges":  "bytes",
                "Content-Length": str(length),
                "Content-Disposition": f'inline; filename="pitchlens_highlights_{match_id[:8]}.mp4"',
            },
        )

    # Full file response
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'attachment; filename="pitchlens_highlights_{match_id[:8]}.mp4"',
        },
    )


@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
