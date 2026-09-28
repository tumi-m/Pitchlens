# Customer-facing claims

Only claims in the "may say" column are allowed in product copy and sales.

| Topic | May say | Must not say | Evidence |
|---|---|---|---|
| Possession | "Share of the time we could follow the ball, with a range" | "Official possession" / a share when coverage < 60% | Analytics withholds below 60%; interval shown |
| Passes | "Detected passes" and accuracy after 20+ attempts | Complete pass totals | Counts cover followed play only |
| Shots / on target | "Shots found for review", "confirmed shots" | Automatic shot totals as fact | Always in the review queue |
| Goals and score | "Score from confirmed goals" | Automatic score | Goals require a reviewer |
| Heatmaps / average positions | "Where each team played, on the calibrated part of the match" | Player-level positions or named players | Needs pitch setup; tracks are not identities |
| Speed / distance / xG | Nothing | Any figure | Not measured |
| Low resolution | "360p and 240p accepted; ball stats may be partial" | "Works on any video" | Footage check before upload |
| Speed of delivery | "Minutes on a GPU for a short test" | A full-match SLA | Only a 20 s GPU run measured |


## Superseding commercial-report policy

Headline event counts now include confirmed events only; pending candidates are
shown separately. "Reviewed pass accuracy" requires 20 confirmed passes. These
counts describe reviewed clips, not complete match totals. Unknown/no confirmed
evidence is shown as a dash rather than a claim of zero events.

"Observed control share" describes followed play. The displayed unseen-play bounds
allocate missing in-play time to either team, conditional on observed assignments
being correct. They are not 95% accuracy bounds and exclude detector/control errors.
The older bootstrap field remains in the export for research compatibility only.
Do not market this as official possession or validated full-match accuracy.
