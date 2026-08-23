"""Builders for the catalogues this project runs on.

None of these run during a build. They are the one-off (and occasionally
re-run) scripts that produce what ``data/`` holds:

* ``download_ldraw_omr``   - the OMR corpus of official sets, as .mpd
* ``build_part_catalog``   - the measured part catalogue, as CSV
* ``build_technique_notes``- what real sets are actually built out of
* ``build_minifig_grips``  - which parts a minifigure hand can hold
"""
