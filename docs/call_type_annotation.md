# Radio call-type annotation guide

## Unit and purpose

Annotate one TRANS segment at a time for analyst triage. Labels are overlapping
topics about the operational subject of the text; they are not a verified
incident type, an identity, or a network edge. Use only segments TRANS marks as
`speech`. Keep audible events and uncertain content in
their existing TRANS classes.

Use a label when the segment explicitly describes the service, operation, or
concern. Do not label from an incidental word, address, station name, location,
or an inferred relationship. Empty labels are valid for acknowledgments,
routine status, addressing, and fragments with no supported topic. Preserve the
source transcript exactly; do not repair ASR text while classifying it.

## Labels

| Label | Apply when the segment explicitly concerns | Do not infer it from |
|---|---|---|
| `fire` | Fire service response, fire, smoke, or fire alarm activity | A station/unit number or an unrelated mention of a building |
| `police` | Law-enforcement service, officer, patrol, or police activity | A person or location name; a traffic stop without a clear law-enforcement cue |
| `ems` | EMS, ambulance, medic, patient care, medical response, or patient transport | A hospital mention by itself or generic “transport” |
| `bus` | Bus, transit, shuttle, or route operations | A vehicle mention without bus/transit context |
| `school` | School, campus, district, or student operations | A school used only as an address or passing location |
| `traffic` | Road/highway flow, collision, obstruction, signal, or closure | An address, street name, radio channel, or “traffic stop” alone |
| `electrical` | Power/electrical equipment, utility wires, outage, or line hazard | A light or powered device without an electrical problem |
| `public_works` | Public infrastructure maintenance such as roads, water, sewer, snow removal, or a public-works crew | A street or utility mentioned only as a location |
| `hazmat` | Hazardous material, chemical/fuel spill, gas leak, or contamination | An industrial site, medic, or unit name alone |
| `weather` | An explicit weather condition or hazard such as storm, flood, snow, high wind, or lightning | A location or road condition without a weather cause |

Assign multiple labels only when each has its own direct support. For example,
a school-bus collision can receive `school`, `bus`, and `traffic` if the segment
explicitly supports all three. Do not repeat a label.

## Evidence and uncertainty

- Give each label one exact quote that contains the words supporting that label.
- Keep station mentions to radio identifiers or callsigns; do not extract
  personal names or locations as stations.
- Keep contacts separate from topics. Only an explicit directed address can
  propose a radio contact, and the independent contact parser must confirm it.
- Use low uncertainty when the words are clear, medium when part of the meaning
  is unresolved, and high when transcription corruption or incompleteness
  prevents a reliable reading. Short text alone is not high uncertainty.
- When ASR corruption prevents a defensible label, leave the labels empty and
  preserve the uncertainty. Do not guess or rewrite the transcript.

## Synthetic examples

These examples are fabricated and exist only to clarify the guide.

| Synthetic segment | Labels | Reason |
|---|---|---|
| `Medic 4 transporting patient to hospital` | `ems` | Medical transport is explicit |
| `Route 8 bus out of service` | `bus` | Bus route operation is explicit |
| `School bus collision blocking eastbound lane` | `school`, `bus`, `traffic` | All three topics are explicit |
| `Power lines down across the road` | `electrical`, `traffic` | Electrical hazard and roadway obstruction are explicit |
| `Copy, en route` | none | Acknowledgment/status without an incident topic |
| `275 Cedar Street` | none | An address alone does not establish a topic |
| `Traffic stop at Cedar and Third` | none | “Traffic stop” alone does not establish road flow or a police service |
| `School, 275 Cedar Street` | none | School is only part of an address/location reference |

## Evaluation requirements

This guide defines the task; it does not validate model or keyword performance.
Before labels are treated as reliable, build an authorized, TRANS-reviewed,
source-grouped sample. Have at least two reviewers label it independently,
adjudicate disagreements, and report per-label precision, recall, F1, support,
and common confusions. Keep private transcripts and annotations on approved
Research storage; publish only synthetic examples and aggregate evaluation
results.
