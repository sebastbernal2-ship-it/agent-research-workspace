# Spikes

Evaluations of external tools. Each folder keeps our own notes, probes and results. The vendored upstream source
snapshots (`*/upstream/`, about 10,000 files) were removed from the working tree; none of the live code uses them.

To restore any snapshot: `git checkout pre-spikes-removal -- spikes/<name>/upstream` (the tag points at the last
commit that still contained them). The READMEs describe the exact upstream versions that were evaluated.
