import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/ui/Navbar";
import { FootageRetention } from "./FootageRetention";

export const metadata: Metadata = { title: "Privacy" };

/** Who receives what, as the code does it (route.ts, server.py, gpu.py, balltrain.py,
 *  billing.py, app/api/infer, lib/firebase, globals.css): change this page with them. */
const PROCESSORS: [string, string][] = [
  ["Vercel", "Hosts this site. Every request, and every piece of an upload, passes through it on the way to the analysis server."],
  ["Railway", "Runs the analysis server, which stores your video, the analysis results, ball labels, venue setups and the billing ledger."],
  [
    "Modal",
    "Our GPU provider. It receives a copy of the video for each analysis. Only for matches whose owner ticked the training opt-in, it also receives labelled frames to train the ball finder; the trained ball model is kept.",
  ],
  ["YouTube", "Only when you paste a YouTube link: the analysis server downloads that video from YouTube."],
  ["Roboflow", "Only when you choose AI frame inspection in manual review: six sample frames from your video, to detect players and the ball."],
  ["Paystack", "At checkout: your email address and payment details. Card details are entered with Paystack, never on Pitchlens."],
  ["Firebase", "Only when sign-in is set up on this site: your email address, name and photo, for your account."],
  ["Google Fonts", "The site's fonts are loaded from Google on each page view."],
];

export default function PrivacyPage() {
  return (
    <>
      <Navbar />
      <main className="max-w-3xl mx-auto px-4 pt-28 pb-16 space-y-6">
        <header className="space-y-3">
          <h1 className="text-3xl font-bold text-pitch-white">Privacy</h1>
          <p className="text-pitch-muted">What happens to your footage and your data when you use Pitchlens.</p>
        </header>

        <section className="glass-card p-6 space-y-3">
          <h2 className="text-lg font-semibold">What it is used for</h2>
          <p className="text-sm text-pitch-muted">
            Your footage is used to analyse your match: finding the players and the ball, and building your report.
          </p>
          <p className="text-sm text-pitch-muted">
            The ball finder is improved only from matches whose owner ticks the training opt-in in the ball labeller.
            It starts unticked, and you can untick it at any time.
          </p>
        </section>

        <section className="glass-card p-6 space-y-3">
          <h2 className="text-lg font-semibold">Who receives what</h2>
          <dl className="space-y-3 text-sm">
            {PROCESSORS.map(([name, what]) => (
              <div key={name}>
                <dt className="font-medium text-pitch-white">{name}</dt>
                <dd className="text-pitch-muted">{what}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="glass-card p-6 space-y-3">
          <h2 className="text-lg font-semibold">How long it is kept</h2>
          <ul className="list-disc pl-5 space-y-2 text-sm text-pitch-muted">
            <li>
              <FootageRetention />
            </li>
            <li>Label stills (frames cut from your video for ball labelling) are deleted with the footage.</li>
            <li>Results, ball labels and venue setups made from a match stay until you delete the match.</li>
            <li>
              Training copies of labelled frames exist only while a training run is in progress and are deleted when
              it ends. A run is discarded if a match in it is deleted or its owner withdraws consent. A model already
              trained keeps what it learned.
            </li>
            <li>
              This browser keeps a random key, in its storage and in a cookie that lasts one year, so that only it can
              see its matches.
            </li>
            <li>
              The billing ledger keeps a one-way hash of your email address and of that browser key, with each
              order&apos;s reference, amount and credits. It stays when you delete a match. Payments run in test mode
              only.
            </li>
          </ul>
        </section>

        <section className="glass-card p-6 space-y-3">
          <h2 className="text-lg font-semibold">How to delete</h2>
          <p className="text-sm text-pitch-muted">
            In{" "}
            <Link href="/dashboard" className="underline hover:text-pitch-white">
              Your matches
            </Link>
            , choose <strong className="text-pitch-white">Delete analysis and footage</strong>. This removes the
            video, its label stills, the results, labels and venue setups made from that match, and its frames from a
            training run in progress. Deleting does not need the access code. A re-analysis of a match is listed
            separately: delete it too to remove its copy of the footage.
          </p>
        </section>
      </main>
    </>
  );
}
