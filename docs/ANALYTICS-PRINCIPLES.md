# Analytics principles (from the Football Analytics library)

The `Football Analytics.zip` library (StatsBomb, 11tegen11, Michael Caley, OSC,
James Grayson, Mark Taylor, Dan Altman, Danny Page and others) shaped what
Pitchlens computes and how the report presents it. Many library entries are
link stubs; the points below come from the articles and charts with content.

## What the engine computes (pipeline 1.5)

| Metric | Definition adapted to camera data | Library source | Caveat shown |
| --- | --- | --- | --- |
| Possessions | Spells of consecutive stable control by one kit. A spell survives up to 3 s of unseen ball, and ends on opponent control or a camera cut. | StatsBomb, *Analysing Leicester's Attack*: "a passage of play where a team maintains unbroken control of the ball" | Only observed spells count |
| Avg / longest possession, passes per possession | From those spells | StatsBomb possession analyses; Caley's "established possession" idea | Pass counts are lower bounds |
| Ball control share | Share of observed control time | Mark Taylor, *TSR repeatability*; OSC, *How Not to Interpret Stats* | Describes style, not quality; withheld below 20% ball-control coverage; plausible range from a Wilson interval over possessions |
| Passes allowed per ball won | Opposition pass candidates ÷ turnovers won | Colin Trainor (StatsBomb), *Measuring the Intensity of a High Press* (PPDA) | Not PPDA: no tackles or pitch zones are measured; single matches are noisy |
| Control over time | Cumulative control seconds per kit, step lines with end labels | 11tegen11 *xG Time Plot* | Describes this match only |

## What Pitchlens does not claim

- xG or chance quality: needs shot location, angle, assist type, body part (Caley).
- PPDA, field tilt, territory, space control: need calibrated pitch coordinates and typed defensive actions.
- Player ratings, per-90s, radars, player passing networks: track IDs are not player identities; radars need population percentiles and mislead with axis order.
- Team strength or future form from one match: regression to the mean (Grayson); small samples (OSC).

## Presentation rules

1. Show spread, not only a point estimate (Danny Page, *Exploring Variance in xG*).
2. State data coverage and thresholds on each panel (pass-map conventions).
3. Say which direction is good (StatsBomb pressing graphs: lower PPDA = higher press).
4. Label lines directly instead of legends (xG time plot).
5. No radars.

## Next, with small extensions

- Manually tagged goals and shots → game-state splits (ahead/level/behind) and total shots ratio (OSC *Game States*; Grayson on TSR).
- Pass motifs (ABAB, ABCB…) from within-sequence track IDs (Perdomo, *Quantifying Passing Subsequences*).
- Fixed-camera pitch calibration → territory and speed metrics.
