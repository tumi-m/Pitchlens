'use client';
import { useState, useCallback, useRef } from 'react';
import { useDropzone } from 'react-dropzone';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import { Upload, Film, X, Plus, Trash2, Loader2, CheckCircle2 } from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { useAuthContext } from '@/components/auth/AuthProvider';
import { uploadVideo } from '@/lib/firebase/storage';
import { createMatch } from '@/lib/firebase/firestore';
import { SOCCER_TRIVIA, formatFileSize, estimateETA } from '@/lib/utils/analytics';
import { cn } from '@/lib/utils/cn';
import toast from 'react-hot-toast';
import type { Player } from '@/lib/types';

const MAX_SIZE = 500 * 1024 * 1024; // 500MB

type Stage = 'form' | 'uploading' | 'analyzing';

export default function UploadPage() {
  const { user } = useAuthContext();
  const router = useRouter();
  const startTimeRef = useRef<number>(0);

  const [stage, setStage] = useState<Stage>('form');
  const [file, setFile] = useState<File | null>(null);
  const [homeTeam, setHomeTeam] = useState('Home Team');
  const [awayTeam, setAwayTeam] = useState('Away Team');
  const [homeColor, setHomeColor] = useState('#FF0000');
  const [awayColor, setAwayColor] = useState('#0000FF');
  const [homePlayers, setHomePlayers] = useState<Player[]>([{ id: '1', name: '', jerseyColor: '#FF0000', position: '' }]);
  const [awayPlayers, setAwayPlayers] = useState<Player[]>([{ id: '1', name: '', jerseyColor: '#0000FF', position: '' }]);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadETA, setUploadETA] = useState('--');
  const [triviaIndex, setTriviaIndex] = useState(0);
  const [matchId, setMatchId] = useState<string | null>(null);

  const onDrop = useCallback((accepted: File[]) => {
    const f = accepted[0];
    if (!f) return;
    if (f.size > MAX_SIZE) { toast.error('File exceeds 500MB limit'); return; }
    if (!f.type.includes('video/mp4') && !f.name.endsWith('.mp4')) {
      toast.error('Only MP4 files are supported');
      return;
    }
    setFile(f);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'video/mp4': ['.mp4'] },
    maxFiles: 1,
    disabled: stage !== 'form',
  });

  const addPlayer = (side: 'home' | 'away') => {
    const players = side === 'home' ? homePlayers : awayPlayers;
    if (players.length >= 10) { toast.error('Maximum 10 players per team'); return; }
    const newPlayer: Player = {
      id: Date.now().toString(),
      name: '',
      jerseyColor: side === 'home' ? homeColor : awayColor,
      position: '',
    };
    side === 'home'
      ? setHomePlayers([...homePlayers, newPlayer])
      : setAwayPlayers([...awayPlayers, newPlayer]);
  };

  const updatePlayer = (side: 'home' | 'away', id: string, field: keyof Player, value: string) => {
    const setter = side === 'home' ? setHomePlayers : setAwayPlayers;
    const players = side === 'home' ? homePlayers : awayPlayers;
    setter(players.map((p) => (p.id === id ? { ...p, [field]: value } : p)));
  };

  const removePlayer = (side: 'home' | 'away', id: string) => {
    const setter = side === 'home' ? setHomePlayers : setAwayPlayers;
    const players = side === 'home' ? homePlayers : awayPlayers;
    if (players.length <= 1) return;
    setter(players.filter((p) => p.id !== id));
  };

  const handleUpload = async () => {
    if (!file || !user) { toast.error('Please sign in and select a file'); return; }

    setStage('uploading');
    startTimeRef.current = Date.now();

    try {
      // Create match doc first to get matchId
      const id = await createMatch({
        userId: user.uid,
        title: `${homeTeam} vs ${awayTeam}`,
        homeTeamName: homeTeam,
        awayTeamName: awayTeam,
        videoUrls: [],
        status: 'uploading',
      });
      setMatchId(id);

      // Rotate trivia every 5s during upload
      const triviaTimer = setInterval(() => {
        setTriviaIndex((i) => (i + 1) % SOCCER_TRIVIA.length);
      }, 5000);

      uploadVideo(
        user.uid,
        id,
        file,
        ({ percentage, bytesTransferred, totalBytes }) => {
          setUploadProgress(percentage);
          setUploadETA(estimateETA(bytesTransferred, totalBytes, startTimeRef.current));
        },
        (downloadUrl) => {
          clearInterval(triviaTimer);
          setStage('analyzing');
          // The Cloud Function will trigger automatically from storage upload
          // Redirect to dashboard to watch real-time updates
          setTimeout(() => router.push(`/dashboard/${id}`), 2000);
        },
        (err) => {
          clearInterval(triviaTimer);
          toast.error(`Upload failed: ${err.message}`);
          setStage('form');
        }
      );
    } catch (err: any) {
      toast.error(err.message || 'Failed to start upload');
      setStage('form');
    }
  };

  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <AnimatePresence mode="wait">
            {stage === 'form' && (
              <motion.div
                key="form"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                className="space-y-8"
              >
                <div>
                  <h1 className="text-3xl font-bold text-pitch-white mb-2">Upload a Match</h1>
                  <p className="text-pitch-muted">Upload your football match footage for AI-powered analytics via Roboflow YOLOv8.</p>
                </div>

                {/* Drop zone */}
                <div
                  {...getRootProps()}
                  className={cn(
                    'border-2 border-dashed rounded-2xl p-12 text-center cursor-pointer transition-all',
                    isDragActive
                      ? 'border-pitch-green bg-pitch-green/5'
                      : 'border-pitch-indigo-soft/40 hover:border-pitch-indigo-glow/60 hover:bg-pitch-indigo-deep/30',
                    file && 'border-pitch-green/50 bg-pitch-green/5'
                  )}
                >
                  <input {...getInputProps()} />
                  {file ? (
                    <div className="flex flex-col items-center gap-3">
                      <div className="w-14 h-14 bg-pitch-green/20 rounded-xl flex items-center justify-center">
                        <Film size={28} className="text-pitch-green" />
                      </div>
                      <div>
                        <p className="text-pitch-white font-medium">{file.name}</p>
                        <p className="text-pitch-muted text-sm">{formatFileSize(file.size)}</p>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); setFile(null); }}
                        className="flex items-center gap-1.5 text-pitch-muted hover:text-red-400 text-sm transition-colors"
                      >
                        <X size={14} /> Remove
                      </button>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-4">
                      <motion.div
                        animate={{ y: isDragActive ? -8 : 0 }}
                        className="w-16 h-16 bg-pitch-indigo-soft/20 rounded-2xl flex items-center justify-center"
                      >
                        <Upload size={32} className="text-pitch-indigo-glow" />
                      </motion.div>
                      <div>
                        <p className="text-pitch-white font-medium">
                          {isDragActive ? 'Drop it here' : 'Drag & drop your match video'}
                        </p>
                        <p className="text-pitch-muted text-sm mt-1">MP4 format · Max 500MB · 1080p recommended · 5-a-side or 11-a-side</p>
                      </div>
                      <span className="pitch-button-secondary text-sm px-5 py-2">Browse Files</span>
                    </div>
                  )}
                </div>

                {/* Team setup */}
                <div className="grid sm:grid-cols-2 gap-6">
                  {(['home', 'away'] as const).map((side) => (
                    <TeamSetup
                      key={side}
                      side={side}
                      teamName={side === 'home' ? homeTeam : awayTeam}
                      setTeamName={side === 'home' ? setHomeTeam : setAwayTeam}
                      teamColor={side === 'home' ? homeColor : awayColor}
                      setTeamColor={side === 'home' ? setHomeColor : setAwayColor}
                      players={side === 'home' ? homePlayers : awayPlayers}
                      onAddPlayer={() => addPlayer(side)}
                      onUpdatePlayer={(id, field, value) => updatePlayer(side, id, field, value)}
                      onRemovePlayer={(id) => removePlayer(side, id)}
                    />
                  ))}
                </div>

                <div className="space-y-3">
                  <button
                    onClick={handleUpload}
                    disabled={!file || !user}
                    className="pitch-button-primary w-full py-3.5 text-base"
                  >
                    {!user ? 'Sign in to Upload' : !file ? 'Select a video first' : '⚽ Analyse Match'}
                  </button>
                  <p className="text-center text-pitch-muted text-xs">
                    Powered by{' '}
                    <a href="https://roboflow.com" target="_blank" rel="noopener noreferrer" className="text-pitch-indigo-glow hover:underline">Roboflow</a>
                    {' '}YOLOv8 · ByteTrack · jsPDF
                  </p>
                </div>
              </motion.div>
            )}

            {stage === 'uploading' && (
              <motion.div
                key="uploading"
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                className="flex flex-col items-center justify-center min-h-[60vh] text-center gap-8"
              >
                <ProgressRing progress={uploadProgress} />
                <div>
                  <p className="text-pitch-white font-semibold text-lg">Uploading match footage…</p>
                  <p className="text-pitch-muted text-sm mt-1">ETA: {uploadETA} · {uploadProgress}% complete</p>
                </div>
                <div className="glass-card px-6 py-4 max-w-sm">
                  <p className="text-pitch-muted text-xs italic">
                    💡 {SOCCER_TRIVIA[triviaIndex]}
                  </p>
                </div>
              </motion.div>
            )}

            {stage === 'analyzing' && (
              <motion.div
                key="analyzing"
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                className="flex flex-col items-center justify-center min-h-[60vh] text-center gap-6"
              >
                <div className="w-20 h-20 bg-pitch-green/20 rounded-full flex items-center justify-center">
                  <CheckCircle2 size={40} className="text-pitch-green" />
                </div>
                <div>
                  <p className="text-pitch-white font-bold text-2xl">Upload Complete!</p>
                  <p className="text-pitch-muted mt-2">Redirecting to your live dashboard…</p>
                </div>
                <Loader2 size={24} className="animate-spin text-pitch-indigo-glow" />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </main>
    </>
  );
}

function ProgressRing({ progress }: { progress: number }) {
  const radius = 60;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (progress / 100) * circumference;

  return (
    <div className="relative w-40 h-40 flex items-center justify-center">
      <svg className="absolute inset-0 w-full h-full -rotate-90" viewBox="0 0 160 160">
        <circle cx="80" cy="80" r={radius} fill="none" stroke="rgba(79,79,186,0.2)" strokeWidth="8" />
        <motion.circle
          cx="80" cy="80" r={radius}
          fill="none"
          stroke="#2ECC71"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transition={{ duration: 0.3 }}
        />
      </svg>
      <span className="text-3xl font-bold text-pitch-white font-mono">{progress}%</span>
    </div>
  );
}

interface TeamSetupProps {
  side: 'home' | 'away';
  teamName: string;
  setTeamName: (v: string) => void;
  teamColor: string;
  setTeamColor: (v: string) => void;
  players: Player[];
  onAddPlayer: () => void;
  onUpdatePlayer: (id: string, field: keyof Player, value: string) => void;
  onRemovePlayer: (id: string) => void;
}

function TeamSetup({ side, teamName, setTeamName, teamColor, setTeamColor, players, onAddPlayer, onUpdatePlayer, onRemovePlayer }: TeamSetupProps) {
  return (
    <div className="glass-card p-5 space-y-4">
      <div className="flex items-center gap-3">
        <input
          type="color"
          value={teamColor}
          onChange={(e) => setTeamColor(e.target.value)}
          className="w-10 h-10 rounded-lg border border-pitch-indigo-soft/30 bg-transparent cursor-pointer p-0.5"
          aria-label={`${side} team colour`}
        />
        <input
          type="text"
          value={teamName}
          onChange={(e) => setTeamName(e.target.value)}
          placeholder={side === 'home' ? 'Home Team' : 'Away Team'}
          className="flex-1 bg-pitch-indigo-deep/50 border border-pitch-indigo-soft/30 rounded-lg
            px-3 py-2 text-pitch-white text-sm focus:outline-none focus:border-pitch-green transition-colors"
        />
      </div>
      <div className="space-y-2 max-h-48 overflow-y-auto no-scrollbar">
        {players.map((p) => (
          <div key={p.id} className="flex items-center gap-2">
            <input
              type="text"
              value={p.name}
              onChange={(e) => onUpdatePlayer(p.id, 'name', e.target.value)}
              placeholder={`Player ${players.indexOf(p) + 1}`}
              className="flex-1 bg-pitch-black/40 border border-pitch-indigo-soft/20 rounded-lg
                px-2.5 py-1.5 text-pitch-white text-xs focus:outline-none focus:border-pitch-green/50"
            />
            <button
              onClick={() => onRemovePlayer(p.id)}
              disabled={players.length <= 1}
              className="text-pitch-muted hover:text-red-400 disabled:opacity-30 transition-colors"
              aria-label="Remove player"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
      <button
        onClick={onAddPlayer}
        className="flex items-center gap-1.5 text-pitch-muted hover:text-pitch-green text-xs transition-colors"
      >
        <Plus size={14} /> Add Player
      </button>
    </div>
  );
}
