"use client";

import { useState, useCallback, useEffect } from 'react';
import { useDropzone } from 'react-dropzone';
import { useRouter } from 'next/navigation';
import { UploadCloud, CheckCircle2, Loader2, Sparkles, AlertCircle, Zap, Eye, TrendingUp } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import PitchIllustration from '@/components/PitchIllustration';

const TRIVIA = [
  "YOLOv11m detects players across camera angles with BoT-SORT ID tracking.",
  "Pitch homography converts pixel coordinates to real-world metres on a 42×25m surface.",
  "Zone pressure analysis maps where the ball dwells — replacing random momentum charts.",
  "The Kalman filter bridges up to 15 frames of ball occlusion without losing position.",
  "jersey k-means clustering assigns players to home/away by dominant HSV colour.",
  "Pass accuracy is computed by tracking ball possession changes between tracked IDs.",
  "xG is velocity-weighted — shots from distance get penalised automatically.",
];

const FEATURES = [
  { icon: Eye, label: "YOLOv11m Detection", sub: "Players + ball across angles" },
  { icon: TrendingUp, label: "Live xG Model", sub: "Velocity-weighted expected goals" },
  { icon: Zap, label: "BoT-SORT Tracking", sub: "Appearance embeddings, no ID switches" },
];

const UPLOAD_PHASE_MAX = 20;

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [matchId, setMatchId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("Waiting for upload...");
  const [triviaIndex, setTriviaIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<'upload' | 'processing'>('upload');

  const onDrop = useCallback((acceptedFiles: File[]) => {
    if (acceptedFiles.length > 0) {
      setFile(acceptedFiles[0]);
      setError(null);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'video/mp4': ['.mp4'], 'video/quicktime': ['.mov'], 'video/*': [] },
    maxFiles: 1,
  });

  const handleProcess = () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    setProgress(0);
    setPhase('upload');
    setStatusMessage("Uploading video to backend...");

    const formData = new FormData();
    formData.append('video', file);

    const xhr = new XMLHttpRequest();

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const uploadPct = Math.round((e.loaded / e.total) * UPLOAD_PHASE_MAX);
        setProgress(uploadPct);
        const mb = (e.loaded / 1024 / 1024).toFixed(1);
        const total = (e.total / 1024 / 1024).toFixed(1);
        setStatusMessage(`Uploading... ${mb} / ${total} MB`);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          setMatchId(data.matchId);
          setPhase('processing');
          setProgress(UPLOAD_PHASE_MAX);
          setStatusMessage("Video received. Starting CV analysis...");
        } catch {
          setError("Backend returned invalid response. Is the server running?");
          setUploading(false);
        }
      } else {
        setError(`Backend error ${xhr.status}. Check that the server is running on port 8000.`);
        setUploading(false);
      }
    };

    xhr.onerror = () => {
      setError("Cannot reach backend at localhost:8000. Run: uvicorn app:app --reload");
      setUploading(false);
    };

    xhr.open('POST', 'http://localhost:8000/process-match');
    xhr.send(formData);
  };

  useEffect(() => {
    if (!matchId) return;

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`http://localhost:8000/match/${matchId}`);
        const data = await res.json();

        if (data.status === 'error') {
          clearInterval(interval);
          setError(`Processing failed: ${data.message}`);
          setUploading(false);
          return;
        }

        const mapped = UPLOAD_PHASE_MAX + Math.round((data.progress / 100) * (100 - UPLOAD_PHASE_MAX));
        setProgress(Math.min(100, mapped));
        setStatusMessage(data.message || "Processing...");

        if (data.status === 'completed') {
          clearInterval(interval);
          setProgress(100);
          setTimeout(() => router.push(`/dashboard/${matchId}`), 600);
        }
      } catch (err) {
        console.error("Polling error", err);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [matchId, router]);

  useEffect(() => {
    if (!uploading) return;
    const interval = setInterval(() => {
      setTriviaIndex(i => (i + 1) % TRIVIA.length);
    }, 4500);
    return () => clearInterval(interval);
  }, [uploading]);

  const circumference = 2 * Math.PI * 80;

  return (
    <div className="min-h-[calc(100vh-4rem)] flex flex-col items-center justify-center p-6 relative overflow-hidden">
      {/* Background orbs */}
      <div className="absolute top-1/3 left-1/4 w-[500px] h-[500px] bg-primary/8 rounded-full blur-[160px] -z-10" />
      <div className="absolute bottom-1/4 right-1/3 w-[400px] h-[400px] bg-accent/8 rounded-full blur-[140px] -z-10" />

      <AnimatePresence mode="wait">
        {!uploading ? (
          /* ── Upload form ── */
          <motion.div
            key="upload-form"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -16 }}
            transition={{ duration: 0.45 }}
            className="w-full max-w-5xl grid grid-cols-1 lg:grid-cols-2 gap-8 items-center"
          >
            {/* Left — Pitch illustration */}
            <motion.div
              initial={{ opacity: 0, x: -24 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.1, duration: 0.6 }}
              className="hidden lg:flex flex-col gap-6"
            >
              <div className="glass-card rounded-2xl p-6 overflow-hidden relative">
                <div className="absolute inset-0 bg-gradient-to-br from-primary/5 to-accent/5" />
                <PitchIllustration
                  className="w-full"
                  animated
                  showBall
                  showPlayers
                />
                <div className="absolute top-4 left-4 px-2 py-1 rounded-md bg-accent/20 border border-accent/30">
                  <span className="text-accent text-xs font-bold tracking-widest uppercase">Live Tracking</span>
                </div>
              </div>

              {/* Feature pills */}
              <div className="grid grid-cols-1 gap-3">
                {FEATURES.map(({ icon: Icon, label, sub }, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, x: -16 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.3 + i * 0.1 }}
                    className="glass-card rounded-xl px-4 py-3 flex items-center gap-3"
                  >
                    <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center shrink-0">
                      <Icon className="w-4 h-4 text-primary" />
                    </div>
                    <div>
                      <p className="text-white text-sm font-semibold">{label}</p>
                      <p className="text-secondary text-xs">{sub}</p>
                    </div>
                  </motion.div>
                ))}
              </div>
            </motion.div>

            {/* Right — Form */}
            <motion.div
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.15, duration: 0.6 }}
              className="glass-card rounded-3xl p-10 shadow-2xl relative overflow-hidden"
            >
              <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-primary via-accent to-primary" />

              <h1 className="text-3xl font-bold mb-1">Upload Match Video</h1>
              <p className="text-secondary mb-8 text-sm">Drop your MP4 recording to begin CV analysis.</p>

              {error && (
                <motion.div
                  initial={{ opacity: 0, y: -8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="mb-5 p-3 bg-danger/10 border border-danger/30 rounded-xl flex items-start gap-3"
                >
                  <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
                  <p className="text-danger text-xs leading-relaxed">{error}</p>
                </motion.div>
              )}

              <div
                {...getRootProps()}
                className={`border-2 border-dashed rounded-2xl p-12 text-center cursor-pointer transition-all duration-300 ${
                  isDragActive
                    ? 'border-primary bg-primary/5 shadow-glow-primary'
                    : 'border-secondary/20 hover:border-primary/40 hover:bg-white/[0.02]'
                }`}
              >
                <input {...getInputProps()} />

                {/* Football illustration inside dropzone */}
                <div className="mb-4 flex justify-center">
                  {file ? (
                    <motion.div
                      initial={{ scale: 0 }}
                      animate={{ scale: 1 }}
                      className="w-16 h-16 rounded-full bg-accent/20 border-2 border-accent/40 flex items-center justify-center"
                    >
                      <CheckCircle2 className="w-8 h-8 text-accent" />
                    </motion.div>
                  ) : (
                    <div className="relative">
                      {/* Soccer ball SVG */}
                      <motion.svg
                        width="64" height="64" viewBox="0 0 64 64"
                        className={`transition-all duration-300 ${isDragActive ? 'scale-110' : ''}`}
                        animate={isDragActive ? { rotate: 360 } : { rotate: 0 }}
                        transition={{ duration: 1, ease: 'linear', repeat: isDragActive ? Infinity : 0 }}
                      >
                        <circle cx="32" cy="32" r="28" fill="#0B1526" stroke="#1E3A5F" strokeWidth="2" />
                        {/* Pentagon pattern */}
                        <polygon
                          points="32,12 40,20 37,30 27,30 24,20"
                          fill="#1E3A5F" stroke="#2D5A8E" strokeWidth="1"
                        />
                        <polygon
                          points="32,52 40,44 37,34 27,34 24,44"
                          fill="#1E3A5F" stroke="#2D5A8E" strokeWidth="1"
                        />
                        <polygon
                          points="12,28 20,24 25,30 22,38 13,38"
                          fill="#1E3A5F" stroke="#2D5A8E" strokeWidth="1"
                        />
                        <polygon
                          points="52,28 44,24 39,30 42,38 51,38"
                          fill="#1E3A5F" stroke="#2D5A8E" strokeWidth="1"
                        />
                        <circle cx="32" cy="32" r="28" fill="none" stroke={isDragActive ? "#4F8CF6" : "#1E3A5F"} strokeWidth="2" />
                      </motion.svg>
                      {isDragActive && (
                        <motion.div
                          className="absolute inset-0 rounded-full bg-primary/30 blur-lg"
                          animate={{ scale: [1, 1.3, 1] }}
                          transition={{ duration: 1, repeat: Infinity }}
                        />
                      )}
                    </div>
                  )}
                </div>

                {file ? (
                  <div className="space-y-1">
                    <p className="font-semibold text-accent">{file.name}</p>
                    <p className="text-secondary text-sm">{(file.size / 1024 / 1024).toFixed(1)} MB — ready to analyze</p>
                  </div>
                ) : (
                  <div>
                    <p className="text-white font-medium mb-1">
                      {isDragActive ? "Drop it here..." : "Drag & drop your match video"}
                    </p>
                    <p className="text-secondary text-sm">MP4 or MOV · any length</p>
                  </div>
                )}
              </div>

              <button
                onClick={handleProcess}
                disabled={!file}
                className={`mt-6 w-full py-4 rounded-xl font-bold text-base transition-all duration-300 ${
                  file
                    ? 'bg-gradient-to-r from-primary to-blue-500 text-white shadow-glow-primary hover:shadow-glow-primary-lg hover:scale-[1.01]'
                    : 'bg-surfaceHover text-secondary/50 cursor-not-allowed'
                }`}
              >
                Analyze Match
              </button>

              <p className="mt-4 text-center text-xs text-secondary/50">
                Powered by YOLOv11m · BoT-SORT · Pitch Homography
              </p>
            </motion.div>
          </motion.div>
        ) : (
          /* ── Processing screen ── */
          <motion.div
            key="processing"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5 }}
            className="w-full max-w-3xl"
          >
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-center">
              {/* Left — Animated pitch (live tracking feel) */}
              <motion.div
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.2 }}
                className="hidden lg:block glass-card rounded-2xl p-4 relative overflow-hidden"
              >
                <div className="absolute inset-0 bg-gradient-to-br from-primary/5 to-transparent" />
                <PitchIllustration
                  className="w-full"
                  animated
                  showBall
                  showPlayers
                />
                {/* Scan line effect */}
                <motion.div
                  className="absolute left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-primary/60 to-transparent"
                  animate={{ top: ['15%', '85%', '15%'] }}
                  transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
                />
                <div className="mt-3 flex items-center gap-2">
                  <motion.div
                    className="w-2 h-2 rounded-full bg-accent"
                    animate={{ opacity: [1, 0.3, 1] }}
                    transition={{ duration: 1.2, repeat: Infinity }}
                  />
                  <span className="text-accent text-xs font-bold tracking-widest uppercase">CV Pipeline Active</span>
                </div>
              </motion.div>

              {/* Right — Progress + status */}
              <div className="flex flex-col items-center text-center gap-6">
                {/* Radial progress */}
                <div className="relative w-40 h-40">
                  <svg className="w-full h-full transform -rotate-90" viewBox="0 0 176 176">
                    <circle cx="88" cy="88" r="80" stroke="#1E293B" strokeWidth="8" fill="transparent" />
                    <motion.circle
                      cx="88" cy="88" r="80"
                      strokeWidth="8" fill="transparent"
                      strokeDasharray={circumference}
                      animate={{ strokeDashoffset: circumference - (circumference * progress) / 100 }}
                      transition={{ duration: 0.6, ease: 'easeOut' }}
                      strokeLinecap="round"
                      stroke="url(#pg)"
                    />
                    <defs>
                      <linearGradient id="pg" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="#4F8CF6" />
                        <stop offset="100%" stopColor="#10B981" />
                      </linearGradient>
                    </defs>
                  </svg>
                  <div className="absolute inset-0 flex flex-col items-center justify-center">
                    <span className="text-3xl font-bold text-white">{progress}%</span>
                    <span className="text-[10px] text-secondary mt-0.5 tracking-widest uppercase">
                      {phase === 'upload' ? 'Uploading' : 'Analyzing'}
                    </span>
                  </div>
                </div>

                {/* Status */}
                <div className="w-full">
                  <div className="flex items-center justify-center gap-2 mb-4">
                    <Loader2 className="w-4 h-4 animate-spin text-primary" />
                    <p className="text-white text-sm font-medium">{statusMessage}</p>
                  </div>

                  {error ? (
                    <motion.div
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className="p-4 bg-danger/10 border border-danger/30 rounded-xl text-left"
                    >
                      <div className="flex gap-2 mb-2">
                        <AlertCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
                        <p className="text-danger text-xs">{error}</p>
                      </div>
                      <button
                        onClick={() => { setUploading(false); setError(null); setProgress(0); setMatchId(null); }}
                        className="text-xs text-secondary underline"
                      >
                        Try again
                      </button>
                    </motion.div>
                  ) : (
                    <AnimatePresence mode="wait">
                      <motion.div
                        key={triviaIndex}
                        initial={{ opacity: 0, y: 8, filter: 'blur(4px)' }}
                        animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                        exit={{ opacity: 0, y: -8, filter: 'blur(4px)' }}
                        transition={{ duration: 0.4 }}
                        className="p-3 glass-card rounded-xl flex items-start gap-2 text-left"
                      >
                        <Sparkles className="w-4 h-4 text-accent shrink-0 mt-0.5" />
                        <p className="text-secondary text-xs leading-relaxed">{TRIVIA[triviaIndex]}</p>
                      </motion.div>
                    </AnimatePresence>
                  )}
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
