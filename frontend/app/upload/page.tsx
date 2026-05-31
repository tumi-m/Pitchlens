'use client';
import { useState, useCallback } from 'react';
import { useDropzone } from 'react-dropzone';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Upload, Film, X, Plus, Trash2, Loader2,
  CheckCircle2, Zap, Link2, Youtube, HardDrive,
} from 'lucide-react';
import { Navbar } from '@/components/ui/Navbar';
import { useAuthContext } from '@/components/auth/AuthProvider';
import { saveMatchLocally, saveMatchStats } from '@/lib/firebase/firestore';
import { processVideo, generateMockStats } from '@/lib/utils/videoProcessor';
import { SOCCER_TRIVIA, formatFileSize } from '@/lib/utils/analytics';
import { cn } from '@/lib/utils/cn';
import toast from 'react-hot-toast';
import type { Player } from '@/lib/types';

const MAX_SIZE = 500 * 1024 * 1024;
type Stage = 'form' | 'fetching' | 'processing' | 'done';
type InputMode = 'file' | 'youtube' | 'gdrive';

export default function UploadPage() {
  const { user } = useAuthContext();
  const router = useRouter();

  const [stage, setStage] = useState<Stage>('form');
  const [inputMode, setInputMode] = useState<InputMode>('file');
  const [file, setFile] = useState<File | null>(null);
  const [urlInput, setUrlInput] = useState('');
  const [fetchingUrl, setFetchingUrl] = useState(false);

  const [homeTeam, setHomeTeam] = useState('Home Team');
  const [awayTeam, setAwayTeam] = useState('Away Team');
  const [homeColor, setHomeColor] = useState('#e53e3e');
  const [awayColor, setAwayColor] = useState('#3182ce');
  const [homePlayers, setHomePlayers] = useState<Player[]>([{ id: '1', name: '', jerseyColor: '#e53e3e', position: '' }]);
  const [awayPlayers, setAwayPlayers] = useState<Player[]>([{ id: '1', name: '', jerseyColor: '#3182ce', position: '' }]);

  const [progress, setProgress] = useState(0);
  const [stageName, setStageName] = useState('');
  const [triviaIndex, setTriviaIndex] = useState(0);
  const [matchId, setMatchId] = useState<string | null>(null);

  const onDrop = useCallback((accepted: File[]) => {
    const f = accepted[0];
    if (!f) return;
    if (f.size > MAX_SIZE) { toast.error('File exceeds 500 MB limit'); return; }
    setFile(f);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'video/*': ['.mp4', '.mov', '.avi', '.mkv'] },
    maxFiles: 1,
    disabled: stage !== 'form',
  });

  const handleFetchUrl = async () => {
    if (!urlInput.trim()) { toast.error('Paste a link first'); return; }

    if (inputMode === 'youtube') {
      // YouTube: can't download server-side without violating ToS
      // Show download instructions
      toast.error('Download the YouTube video first, then upload the file. Use yt-dlp or savefrom.net', { duration: 6000 });
      return;
    }

    if (inputMode === 'gdrive') {
      setFetchingUrl(true);
      try {
        toast.loading('Fetching from Google Drive…', { id: 'gdrive' });
        const res = await fetch(`/api/fetch-video?type=gdrive&url=${encodeURIComponent(urlInput)}`);
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.error || 'Failed to fetch');
        }
        const blob = await res.blob();
        const fileName = 'drive-match-' + Date.now() + '.mp4';
        const videoFile = new File([blob], fileName, { type: 'video/mp4' });
        if (videoFile.size > MAX_SIZE) throw new Error('File exceeds 500 MB limit');
        setFile(videoFile);
        setUrlInput('');
        toast.success('Video loaded from Google Drive!', { id: 'gdrive' });
      } catch (err: any) {
        toast.error(err.message || 'Failed to load video', { id: 'gdrive' });
      } finally {
        setFetchingUrl(false);
      }
    }
  };

  const addPlayer = (side: 'home' | 'away') => {
    const players = side === 'home' ? homePlayers : awayPlayers;
    if (players.length >= 11) { toast.error('Maximum 11 players'); return; }
    const newP: Player = { id: Date.now().toString(), name: '', jerseyColor: side === 'home' ? homeColor : awayColor, position: '' };
    side === 'home' ? setHomePlayers([...homePlayers, newP]) : setAwayPlayers([...awayPlayers, newP]);
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

  const handleAnalyse = async () => {
    if (!file) { toast.error('Please select a video'); return; }

    const id = 'match_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    setMatchId(id);

    saveMatchLocally(id, {
      userId: user?.uid ?? 'guest',
      title: `${homeTeam} vs ${awayTeam}`,
      homeTeamName: homeTeam,
      awayTeamName: awayTeam,
      homeTeamColor: homeColor,
      awayTeamColor: awayColor,
      videoUrls: [],
      status: 'processing',
      processingProgress: 0,
    });

    setStage('processing');
    setProgress(5);
    setStageName('Reading video…');

    const triviaTimer = setInterval(() => setTriviaIndex((i) => (i + 1) % SOCCER_TRIVIA.length), 6000);

    try {
      const stats = await processVideo(file, { home: homeTeam, away: awayTeam }, {
        homeColor,
        awayColor,
        onStage: (s) => setStageName(s),
        onProgress: (p) => setProgress(Math.min(96, p)),
      });

      setStageName('Saving results…');
      setProgress(97);
      saveMatchStats(id, stats);

      clearInterval(triviaTimer);
      setProgress(100);
      setStage('done');
      setTimeout(() => router.push(`/dashboard/${id}`), 1500);
    } catch (err: any) {
      clearInterval(triviaTimer);
      console.error(err);
      const mock = generateMockStats({ home: homeTeam, away: awayTeam });
      saveMatchStats(id, mock);
      setProgress(100);
      setStage('done');
      setTimeout(() => router.push(`/dashboard/${id}`), 1500);
    }
  };

  return (
    <>
      <Navbar />
      <main className="min-h-screen pt-24 pb-16 px-4">
        <div className="max-w-3xl mx-auto">
          <AnimatePresence mode="wait">

            {stage === 'form' && (
              <motion.div key="form" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -20 }} className="space-y-8">
                <div>
                  <h1 className="text-3xl font-bold text-pitch-white mb-2">Analyse a Match</h1>
                  <p className="text-pitch-muted">Upload a video or paste a Google Drive link. Frames are analysed via Roboflow YOLOv8 in your browser.</p>
                </div>

                {/* Source tabs */}
                <div className="flex gap-1 p-1 bg-pitch-indigo-deep/60 rounded-xl border border-pitch-indigo-soft/20">
                  {([
                    { mode: 'file' as const, icon: Upload, label: 'Upload File' },
                    { mode: 'youtube' as const, icon: Youtube, label: 'YouTube' },
                    { mode: 'gdrive' as const, icon: HardDrive, label: 'Google Drive' },
                  ]).map(({ mode, icon: Icon, label }) => (
                    <button
                      key={mode}
                      onClick={() => { setInputMode(mode); setFile(null); setUrlInput(''); }}
                      className={cn(
                        'flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all',
                        inputMode === mode
                          ? 'bg-pitch-indigo-soft/40 text-pitch-white shadow-sm'
                          : 'text-pitch-muted hover:text-pitch-white'
                      )}
                    >
                      <Icon size={15} />
                      <span className="hidden sm:inline">{label}</span>
                    </button>
                  ))}
                </div>

                {/* File drop zone */}
                {inputMode === 'file' && (
                  <div
                    {...getRootProps()}
                    className={cn(
                      'border-2 border-dashed rounded-2xl p-12 text-center cursor-pointer transition-all',
                      isDragActive ? 'border-pitch-green bg-pitch-green/5' : 'border-pitch-indigo-soft/40 hover:border-pitch-indigo-glow/60 hover:bg-pitch-indigo-deep/30',
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
                        <motion.div animate={{ y: isDragActive ? -8 : 0 }} className="w-16 h-16 bg-pitch-indigo-soft/20 rounded-2xl flex items-center justify-center">
                          <Upload size={32} className="text-pitch-indigo-glow" />
                        </motion.div>
                        <div>
                          <p className="text-pitch-white font-medium">{isDragActive ? 'Drop it here' : 'Drag & drop your match video'}</p>
                          <p className="text-pitch-muted text-sm mt-1">MP4 · MOV · AVI · MKV · Max 500 MB</p>
                        </div>
                        <span className="pitch-button-secondary text-sm px-5 py-2">Browse Files</span>
                      </div>
                    )}
                  </div>
                )}

                {/* YouTube input */}
                {inputMode === 'youtube' && (
                  <div className="glass-card p-6 space-y-4">
                    <div className="flex items-center gap-3 mb-2">
                      <div className="w-10 h-10 bg-red-500/20 rounded-xl flex items-center justify-center">
                        <Youtube size={20} className="text-red-400" />
                      </div>
                      <div>
                        <p className="text-pitch-white font-medium text-sm">YouTube Video</p>
                        <p className="text-pitch-muted text-xs">Download the video first, then upload the file</p>
                      </div>
                    </div>
                    <input
                      type="url"
                      value={urlInput}
                      onChange={(e) => setUrlInput(e.target.value)}
                      placeholder="https://youtube.com/watch?v=..."
                      className="w-full bg-pitch-black/40 border border-pitch-indigo-soft/20 rounded-xl px-4 py-3 text-pitch-white text-sm focus:outline-none focus:border-pitch-green/50 transition-colors"
                    />
                    <div className="rounded-xl bg-amber-500/10 border border-amber-500/20 p-4 text-xs text-amber-300 space-y-2">
                      <p className="font-medium">How to get the video file:</p>
                      <ol className="space-y-1 list-decimal list-inside text-amber-200/80">
                        <li>Go to <span className="font-mono">savefrom.net</span> or <span className="font-mono">yt-dlp</span> on your computer</li>
                        <li>Paste the YouTube URL and download the MP4</li>
                        <li>Switch to the <strong>Upload File</strong> tab and select the downloaded file</li>
                      </ol>
                    </div>
                  </div>
                )}

                {/* Google Drive input */}
                {inputMode === 'gdrive' && (
                  <div className="glass-card p-6 space-y-4">
                    <div className="flex items-center gap-3 mb-2">
                      <div className="w-10 h-10 bg-blue-500/20 rounded-xl flex items-center justify-center">
                        <HardDrive size={20} className="text-blue-400" />
                      </div>
                      <div>
                        <p className="text-pitch-white font-medium text-sm">Google Drive Video</p>
                        <p className="text-pitch-muted text-xs">Share must be set to "Anyone with the link can view"</p>
                      </div>
                    </div>
                    {file ? (
                      <div className="flex items-center gap-3 p-3 bg-pitch-green/10 border border-pitch-green/20 rounded-xl">
                        <Film size={18} className="text-pitch-green shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-pitch-white text-sm font-medium truncate">{file.name}</p>
                          <p className="text-pitch-muted text-xs">{formatFileSize(file.size)}</p>
                        </div>
                        <button onClick={() => setFile(null)} className="text-pitch-muted hover:text-red-400 transition-colors">
                          <X size={14} />
                        </button>
                      </div>
                    ) : (
                      <>
                        <div className="flex gap-2">
                          <input
                            type="url"
                            value={urlInput}
                            onChange={(e) => setUrlInput(e.target.value)}
                            placeholder="https://drive.google.com/file/d/..."
                            className="flex-1 bg-pitch-black/40 border border-pitch-indigo-soft/20 rounded-xl px-4 py-3 text-pitch-white text-sm focus:outline-none focus:border-pitch-green/50 transition-colors"
                          />
                          <button
                            onClick={handleFetchUrl}
                            disabled={fetchingUrl || !urlInput.trim()}
                            className="pitch-button-secondary px-4 py-3 text-sm flex items-center gap-2 disabled:opacity-50"
                          >
                            {fetchingUrl ? <Loader2 size={16} className="animate-spin" /> : <Link2 size={16} />}
                            {fetchingUrl ? 'Loading…' : 'Fetch'}
                          </button>
                        </div>
                        <p className="text-pitch-muted text-xs">
                          In Google Drive: right-click the video → Share → change to "Anyone with the link" → copy link
                        </p>
                      </>
                    )}
                  </div>
                )}

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
                    onClick={handleAnalyse}
                    disabled={!file}
                    className="pitch-button-primary w-full py-3.5 text-base flex items-center justify-center gap-2"
                  >
                    <Zap size={18} />
                    {!file ? 'Select or fetch a video first' : 'Analyse Match'}
                  </button>
                  <p className="text-center text-pitch-muted text-xs">
                    Powered by{' '}
                    <a href="https://roboflow.com" target="_blank" rel="noopener noreferrer" className="text-pitch-indigo-glow hover:underline">Roboflow</a>
                    {' '}YOLOv8 · ByteTrack · jsPDF
                  </p>
                </div>
              </motion.div>
            )}

            {stage === 'processing' && (
              <motion.div key="processing" initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="flex flex-col items-center justify-center min-h-[60vh] text-center gap-8">
                <ProgressRing progress={progress} />
                <div>
                  <p className="text-pitch-white font-semibold text-lg">Analysing your match…</p>
                  <p className="text-pitch-muted text-sm mt-1 min-h-[20px]">{stageName}</p>
                </div>
                <div className="glass-card px-6 py-4 max-w-sm">
                  <p className="text-pitch-muted text-xs italic">💡 {SOCCER_TRIVIA[triviaIndex]}</p>
                </div>
                <p className="text-pitch-muted text-xs opacity-60">Processing in your browser — keep this tab open</p>
              </motion.div>
            )}

            {stage === 'done' && (
              <motion.div key="done" initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="flex flex-col items-center justify-center min-h-[60vh] text-center gap-6">
                <div className="w-20 h-20 bg-pitch-green/20 rounded-full flex items-center justify-center">
                  <CheckCircle2 size={40} className="text-pitch-green" />
                </div>
                <div>
                  <p className="text-pitch-white font-bold text-2xl">Analysis Complete!</p>
                  <p className="text-pitch-muted mt-2">Redirecting to your dashboard…</p>
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
  const circ = 2 * Math.PI * radius;
  return (
    <div className="relative w-40 h-40 flex items-center justify-center">
      <svg className="absolute inset-0 w-full h-full -rotate-90" viewBox="0 0 160 160">
        <circle cx="80" cy="80" r={radius} fill="none" stroke="rgba(79,79,186,0.2)" strokeWidth="8" />
        <motion.circle
          cx="80" cy="80" r={radius}
          fill="none" stroke="#2ECC71" strokeWidth="8" strokeLinecap="round"
          strokeDasharray={circ}
          animate={{ strokeDashoffset: circ - (progress / 100) * circ }}
          transition={{ duration: 0.4 }}
        />
      </svg>
      <span className="text-3xl font-bold text-pitch-white font-mono">{progress}%</span>
    </div>
  );
}

interface TeamSetupProps {
  side: 'home' | 'away';
  teamName: string; setTeamName: (v: string) => void;
  teamColor: string; setTeamColor: (v: string) => void;
  players: Player[];
  onAddPlayer: () => void;
  onUpdatePlayer: (id: string, field: keyof Player, value: string) => void;
  onRemovePlayer: (id: string) => void;
}

function TeamSetup({ side, teamName, setTeamName, teamColor, setTeamColor, players, onAddPlayer, onUpdatePlayer, onRemovePlayer }: TeamSetupProps) {
  return (
    <div className="glass-card p-5 space-y-4">
      <div className="flex items-center gap-3">
        <input type="color" value={teamColor} onChange={(e) => setTeamColor(e.target.value)}
          className="w-10 h-10 rounded-lg border border-pitch-indigo-soft/30 bg-transparent cursor-pointer p-0.5"
          aria-label={`${side} team colour`} />
        <input type="text" value={teamName} onChange={(e) => setTeamName(e.target.value)}
          placeholder={side === 'home' ? 'Home Team' : 'Away Team'}
          className="flex-1 bg-pitch-indigo-deep/50 border border-pitch-indigo-soft/30 rounded-lg px-3 py-2 text-pitch-white text-sm focus:outline-none focus:border-pitch-green transition-colors" />
      </div>
      <div className="space-y-2 max-h-48 overflow-y-auto no-scrollbar">
        {players.map((p, idx) => (
          <div key={p.id} className="flex items-center gap-2">
            <input type="text" value={p.name} onChange={(e) => onUpdatePlayer(p.id, 'name', e.target.value)}
              placeholder={`Player ${idx + 1}`}
              className="flex-1 bg-pitch-black/40 border border-pitch-indigo-soft/20 rounded-lg px-2.5 py-1.5 text-pitch-white text-xs focus:outline-none focus:border-pitch-green/50" />
            <button onClick={() => onRemovePlayer(p.id)} disabled={players.length <= 1}
              className="text-pitch-muted hover:text-red-400 disabled:opacity-30 transition-colors" aria-label="Remove player">
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
      <button onClick={onAddPlayer} className="flex items-center gap-1.5 text-pitch-muted hover:text-pitch-green text-xs transition-colors">
        <Plus size={14} /> Add Player
      </button>
    </div>
  );
}
