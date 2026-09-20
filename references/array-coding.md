# Array Coding Reference

`metasurface_array.py` converts an ideal aperture phase into a legal discrete
state matrix. A study may provide `array.phase_deg` directly, or request a
calculated phase gradient using frequency, element spacing, incident direction,
and target direction.

Each state must carry a measured or otherwise justified `phase_deg`; the tool
does not invent device phase values. Quantisation uses circular phase distance
and reports the state matrix, state counts, maximum error, and RMS error.

For a direction-generated map, the current convention is
`phase = sign * 360 f/c * (x * delta_u + y * delta_v) + offset`, reduced to
0--360 degrees. The sign and offset are explicit study fields because CST
coordinate and reflection conventions vary by project. Validate the resulting
map against the full-wave unit-cell codebook before using it for an array.
