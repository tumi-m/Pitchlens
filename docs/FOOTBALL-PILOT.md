# Football model pilot

The optional Roboflow football weights run locally. No hosted API, API key, paid
plan, or private-video transfer was used in this pilot.

The initial detector comparison used twelve preselected frames from two videos.
Ball centres were visually annotated by the assistant before viewing model
predictions; three ambiguous indoor frames were excluded. These annotations are
not independently adjudicated. Detection confidence was 0.15 for both models;
a centre within 6 source pixels (indoor) or 12 pixels (1080p broadcast) counts as
one match, and duplicate/unmatched boxes count as false positives.

On six confidently labelled broadcast frames the existing detector matched two
balls with three false detections. The tiled Roboflow detector matched five with
one false detection. On three confidently labelled indoor frames both matched
one ball; the tiled detector added two false detections. This supports exposing
a separate experimental broadcast profile, not replacing the indoor baseline.

This tiny diagnostic does not establish deployment accuracy. The broadcast clip
is from Roboflow's own example and may overlap the models' training source.
Future training/evaluation must split by match and venue, include low-resolution
indoor recordings, and measure visible-ball recall, false positives, identity
switches, and event precision/recall against independently reviewed labels.

Full 30-second diagnostics were then processed by the same engine used by the
app. Reports distinguish ball coverage from accuracy and continue withholding
possession shares when evidence is insufficient. No goals, xG, physical speed or
verified pass totals are inferred from these tests.

Sources: https://github.com/roboflow/sports/tree/main/examples/soccer and
https://blog.roboflow.com/tracking-ball-sports-computer-vision/ . The downloaded
sample is 2e57b9_0.mp4 from the official example setup script. Exact model hashes
are pinned in backend/scripts/setup_football.py.
