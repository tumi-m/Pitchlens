# Pitchlens vision worker: the same engine as the local worker, packaged for a
# hosted container (Railway, Render, Fly, Cloud Run, any Docker host).
#
#   docker build -f vision.Dockerfile -t pitchlens-vision .
#   docker run -p 8100:8100 -e VISION_SERVICE_TOKEN=... -v pitchlens-data:/data pitchlens-vision
#
# CPU by default. Model weights are downloaded and checksum-verified at build
# time, never during an upload.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    YOLO_CONFIG_DIR=/app/.ultralytics \
    MPLCONFIGDIR=/tmp/matplotlib \
    YOLO_OFFLINE=1 \
    VISION_DATA_DIR=/data/jobs \
    VISION_ALLOWED_HOSTS=* \
    VISION_RETENTION_HOURS=168

WORKDIR /app

# CPU-only PyTorch wheels keep the image ~2 GB instead of ~7 GB of CUDA libraries.
COPY requirements-vision.txt .
RUN pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements-vision.txt \
    # The GUI build of OpenCV needs libGL from apt; the headless build does not.
    && pip uninstall -y opencv-python \
    && pip install --no-deps opencv-python-headless==4.10.0.84 \
    && python -c "import cv2, torch, onnxruntime, ultralytics; print('cv2', cv2.__version__, 'torch', torch.__version__)"

COPY app/ ./app/
COPY scripts/ ./scripts/

# Indoor/small-sided profile (required): YOLO11s players + football ONNX ball model.
RUN python scripts/setup_vision.py
# Broadcast profile (optional): Roboflow's football weights hosted on Google Drive.
# If Drive refuses the download the worker still starts; the UI disables that profile.
ARG INSTALL_BROADCAST=1
RUN if [ "$INSTALL_BROADCAST" = "1" ]; then \
      python scripts/setup_football.py || echo "WARNING: broadcast models not installed; broadcast profile disabled"; \
    fi

RUN mkdir -p /data/jobs "$YOLO_CONFIG_DIR"

EXPOSE 8100
# One worker process: analysis is serialised by design (one job at a time).
CMD ["sh", "-c", "exec uvicorn app.vision.server:app --host 0.0.0.0 --port ${PORT:-8100} --workers 1 --timeout-graceful-shutdown 20"]
