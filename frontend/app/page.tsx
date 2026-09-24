import Link from "next/link";
import { ArrowRight, Film, Flag, Download } from "lucide-react";
import { Navbar } from "@/components/ui/Navbar";
export default function HomePage() {
  return (
    <>
      <Navbar />
      <main className="pt-32 pb-20 px-5">
        <div className="max-w-6xl mx-auto">
          <section className="grid lg:grid-cols-[1.2fr_1fr] gap-12 items-center py-12">
            <div>
              <p className="text-pitch-green text-xs uppercase tracking-[0.25em] font-semibold mb-6">
                For the game after the game
              </p>
              <h1 className="text-5xl sm:text-6xl font-bold tracking-tight leading-[1.08] mb-6">
                See the moment.
                <br />
                <span className="text-pitch-green">Improve the next.</span>
              </h1>
              <p className="text-pitch-muted text-lg max-w-lg leading-relaxed mb-8">
                Run computer vision across your match footage. Detect players
                and the ball, follow kit groups, and inspect automatically
                discovered event candidates.
              </p>
              <div className="flex flex-wrap gap-3">
                <Link href="/upload" className="pitch-button-primary py-3 px-6">
                  Analyse a match <ArrowRight size={18} />
                </Link>
                <Link
                  href="/dashboard"
                  className="pitch-button-secondary py-3 px-6"
                >
                  Your matches
                </Link>
              </div>
              <p className="text-sm text-pitch-muted mt-5">
                No account needed · Manual review keeps video on your device
              </p>
            </div>
            <div className="rounded-3xl border border-pitch-indigo-soft/40 bg-pitch-indigo-deep/30 p-7">
              <div className="flex justify-between text-xs text-pitch-muted mb-6">
                <span>COMPUTER VISION</span>
                <span>WORKFLOW PREVIEW</span>
              </div>
              <svg
                viewBox="0 0 420 270"
                className="w-full"
                role="img"
                aria-label="Football pitch illustration"
              >
                <rect
                  x="10"
                  y="10"
                  width="400"
                  height="250"
                  rx="3"
                  fill="#12322a"
                  stroke="#4e7868"
                />
                <path
                  d="M210 10v250M10 65h60v140H10M410 65h-60v140h60"
                  fill="none"
                  stroke="#4e7868"
                />
                <circle cx="210" cy="135" r="36" fill="none" stroke="#4e7868" />
                <path
                  d="M120 185L200 155L285 80"
                  fill="none"
                  stroke="#65e6a4"
                  strokeWidth="3"
                  strokeDasharray="7 7"
                />
                {[
                  [120, 185],
                  [200, 155],
                  [285, 80],
                ].map(([x, y]) => (
                  <circle key={x} cx={x} cy={y} r="9" fill="#65e6a4" />
                ))}
              </svg>
              <p className="font-semibold mt-6">
                The build-up matters as much as the finish.
              </p>
              <p className="text-sm text-pitch-muted mt-2">
                Inspect the detections behind each observation. See where the
                ball was visible and where the evidence is missing.
              </p>
            </div>
          </section>
          <section className="grid md:grid-cols-3 gap-8 mt-16 border-t border-pitch-indigo-soft/30 pt-10">
            {[
              {
                icon: Film,
                title: "Bring your footage",
                text: "Upload an H.264 MP4. The Pitchlens vision worker analyses the full video with detection models, not sample statistics.",
              },
              {
                icon: Flag,
                title: "Detect and track",
                text: "Automatically detect players and the ball, group kit colours and discover possible passes and turnovers.",
              },
              {
                icon: Download,
                title: "Take your review with you",
                text: "Export a structured review with timestamps and notes. Optional AI inspects sample frames for players and the ball.",
              },
            ].map(({ icon: Icon, title, text }) => (
              <div key={title}>
                <Icon className="text-pitch-green mb-5" size={24} />
                <h2 className="text-lg font-semibold mb-3">{title}</h2>
                <p className="text-pitch-muted text-sm leading-relaxed">
                  {text}
                </p>
              </div>
            ))}
          </section>
          <footer className="mt-20 text-sm text-pitch-muted border-t border-pitch-indigo-soft/30 pt-6">
            Pitchlens · Football review grounded in footage.
          </footer>
        </div>
      </main>
    </>
  );
}
