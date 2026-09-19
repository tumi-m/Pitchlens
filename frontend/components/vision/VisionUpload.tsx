"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, ScanLine, Loader2 } from "lucide-react";
import { Navbar } from "@/components/ui/Navbar";
import { visionJson, VisionJob } from "@/lib/review/vision";

export function VisionUpload({ onManual }: { onManual: () => void }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [available, setAvailable] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [percent, setPercent] = useState(0);
  const [profiles, setProfiles] = useState<string[]>(["general"]);
  const [profile, setProfile] = useState("general");
  const [fps, setFps] = useState("3");
  const xhr = useRef<XMLHttpRequest | null>(null);
  useEffect(() => {
    visionJson<{ available: boolean; profiles?: string[] }>("health")
      .then((x) => { setAvailable(x.available); setProfiles(x.profiles ?? ["general"]); })
      .catch(() => setAvailable(false));
    return () => xhr.current?.abort();
  }, []);
  async function submit() {
    if (!file) return;
    setError("");
    setBusy(true);
    setPercent(0);
    try {
      const job = await new Promise<VisionJob>((resolve, reject) => {
        const req = new XMLHttpRequest();
        xhr.current = req;
        req.open(
          "POST",
          `/api/vision/jobs?title=${encodeURIComponent(title.trim() || file.name)}&profile=${profile}&fps=${fps}`,
        );
        req.setRequestHeader(
          "Content-Type",
          file.type || "application/octet-stream",
        );
        req.upload.onprogress = (e) => {
          if (e.lengthComputable)
            setPercent(Math.round((e.loaded / e.total) * 100));
        };
        req.onload = () => {
          try {
            const data = JSON.parse(req.responseText);
            if (req.status >= 200 && req.status < 300) resolve(data);
            else reject(new Error(data.detail || "Upload failed"));
          } catch {
            reject(new Error("Invalid worker response"));
          }
        };
        req.onerror = () =>
          reject(new Error("Could not reach the vision worker."));
        req.onabort = () => reject(new Error("Upload cancelled."));
        req.send(file);
      });
      xhr.current = null;
      router.push(`/vision/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to start analysis");
      setBusy(false);
      xhr.current = null;
    }
  }
  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-28 pb-16 px-4">
        <div className="max-w-3xl mx-auto space-y-7">
          <div>
            <p className="text-pitch-green text-xs tracking-widest uppercase mb-3">
              Computer vision · Local processing
            </p>
            <h1 className="text-4xl font-bold mb-4">
              Let the footage do the talking.
            </h1>
            <p className="text-pitch-muted">
              Detect players and the ball across the video, follow track IDs,
              separate kit colours and find possible passes and changes of
              possession automatically.
            </p>
          </div>
          <div className="grid sm:grid-cols-3 gap-3">
            {[
              "Player + ball detection",
              "Kit grouping + tracking",
              "Evidence-linked analytics",
            ].map((x) => (
              <div key={x} className="glass-card p-4 text-sm">
                <ScanLine size={19} className="text-pitch-green mb-3" />
                {x}
              </div>
            ))}
          </div>
          {available === false && (
            <div
              role="alert"
              className="border border-amber-500/40 bg-amber-500/10 rounded-xl p-4 text-sm space-y-2"
            >
              <p className="font-semibold">
                Start the local vision worker to analyse a match.
              </p>
              <p>
                This runs on your computer with downloaded model weights. No
                Roboflow key is needed.
              </p>
              <code className="block break-all">
                python scripts/start_vision.py
              </code>
              <p>
                Run from the backend folder after following docs/VISION.md. Then
                reload this page.
              </p>
            </div>
          )}
          <label className="block glass-card border-2 border-dashed border-pitch-green/40 p-10 text-center cursor-pointer">
            <Upload className="mx-auto text-pitch-green mb-4" />
            <span className="block font-semibold break-all">
              {file ? file.name : "Choose your match video"}
            </span>
            <span className="text-sm text-pitch-muted block mt-2">
              MP4 · Up to 500 MB · Video stays on this computer
            </span>
            <input
              aria-label="Video for computer vision"
              type="file"
              accept="video/mp4,.mp4"
              disabled={busy}
              className="mt-5 max-w-full text-sm"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                if (f.size > 500 * 1024 * 1024) {
                  setFile(null);
                  setError("Video exceeds 500 MB");
                  return;
                }
                if (!/\.mp4$/i.test(f.name)) {
                  setFile(null);
                  setError("Choose an MP4 video.");
                  return;
                }
                setFile(f);
                setTitle(f.name.replace(/\.mp4$/i, ""));
                setError("");
              }}
            />
          </label>
          <label className="block text-sm">
            Match title
            <input
              aria-label="Analysis title"
              className="pitch-input w-full mt-2"
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={busy}
            />
          </label>
          <p className="text-sm text-pitch-muted">
            Processing time depends on video length and hardware. The report
            shows detection coverage and unknown time. Kit groups need your team
            names; automatic pass candidates are estimates. Score, xG and
            physical speed are not yet measured.
          </p>
          <div className="grid sm:grid-cols-2 gap-4">
            <label className="text-sm">Footage type
              <select className="pitch-input w-full mt-2" value={profile}
                disabled={busy} onChange={(e) => setProfile(e.target.value)}>
                <option value="general">Indoor / small-sided · baseline</option>
                <option value="broadcast" disabled={!profiles.includes("broadcast")}>
                  Full-pitch broadcast · experimental
                </option>
              </select>
            </label>
            <label className="text-sm">Analysis detail
              <select className="pitch-input w-full mt-2" value={fps}
                disabled={busy} onChange={(e) => setFps(e.target.value)}>
                <option value="3">Quick · about 3 frames/sec</option>
                <option value="6">Balanced · about 6 frames/sec</option>
                <option value="10">Detailed · about 10 frames/sec</option>
              </select>
            </label>
          </div>
          <p className="text-xs text-pitch-muted">
            Detailed analysis follows fast movement more closely and takes longer.
            The broadcast model has not been validated for indoor matches.
          </p>
          {error && (
            <p role="alert" className="text-red-300">
              {error}
            </p>
          )}
          <button
            disabled={!file || !available || busy}
            onClick={submit}
            className="pitch-button-primary w-full py-4"
          >
            {busy ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                {percent < 100
                  ? `Uploading to local worker · ${percent}%`
                  : "Opening video…"}
              </>
            ) : (
              <>
                Analyse video automatically <ScanLine size={18} />
              </>
            )}
          </button>
          {busy && (
            <button
              onClick={() => xhr.current?.abort()}
              className="pitch-button-secondary"
            >
              Cancel upload
            </button>
          )}
          <button
            onClick={onManual}
            disabled={busy}
            className="text-sm text-pitch-muted underline"
          >
            Open manual review instead
          </button>
        </div>
      </main>
    </>
  );
}
