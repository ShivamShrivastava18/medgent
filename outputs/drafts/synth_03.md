# Discharge Summary Draft

_Generated 2026-06-03T11:02:48.790215+00:00_  ·  source: `data/synthetic/synth_03/source.pdf`

**Agent metrics:** 47 iterations, 31 tool calls

**Safety flags:** 1

## Patient demographics  —  **FLAGGED**
> Unable to locate patient's age, sex, or MRN after multiple search attempts. Only the patient's name 'synth_03' was found.

## Admission date
2026-04-21

_Sources: p.1_

## Discharge date
2026-04-23

_Sources: p.3_

## Principal diagnosis
Enteric fever (Typhoid)

_Sources: p.1, p.6_

## Secondary diagnoses  —  **MISSING**
> agent did not commit a value

## Hospital course
Hospital course summary: The patient was admitted for enteric fever (typhoid) with fever spikes. Blood cultures from admission were positive for Salmonella Typhi, which was sensitive to ceftriaxone. The patient was started on IV Ceftriaxone. The patient became afebrile on hospital day 2.

_Sources: p.1, p.3, p.4_

## Procedures  —  **MISSING**
> agent did not commit a value

## Admission medications
- name_as_written=MONOCEF, dose=1g, route=IV, frequency=1-0-1

_Sources: p.5_

## Discharge medications
- name_as_written=AZITHRAL, dose=500mg, frequency=1-0-0, duration=5 days
- name_as_written=PAN, dose=40mg, frequency=1-0-0
- name_as_written=DOLO, dose=650mg, frequency=SOS
- name_as_written=EMSET, dose=4mg, frequency=SOS

_Sources: p.5_

## Medication changes

_Sources: p.5_

## Drug interactions

_Sources: p.6_

## Allergies
- No Known Drug Allergies

_Sources: p.1_

## Follow-up instructions
['Review with CBC in 5 days']

_Sources: p.6_

## Pending results
- {'name': 'Complete Blood Count', 'status': 'pending'}

_Sources: p.1_

## Discharge condition
Stable. Afebrile for the last 24 hours.

_Sources: p.6_

## Safety flags raised
- **[medium]** `demographics` — Unable to locate patient's age, sex, or MRN after multiple search attempts. Only the patient's name 'synth_03' was found.


---
*This is a DRAFT for clinician review. Sections marked PENDING, MISSING, FLAGGED, or CONFLICTING require explicit clinician action before finalizing.*