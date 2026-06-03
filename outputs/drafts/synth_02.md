# Discharge Summary Draft

_Generated 2026-06-03T05:44:08.935904+00:00_  ·  source: `data/synthetic/synth_02/source.pdf`

**Agent metrics:** 25 iterations, 16 tool calls

**Safety flags:** 6

## Patient demographics

_Sources: p.3_

## Admission date
2023-08-12

_Sources: p.1_

## Discharge date
2023-08-13

_Sources: p.6_

## Principal diagnosis
Acute heart failure exacerbation

_Sources: p.2_

## Secondary diagnoses  —  **MISSING**
> Searches for 'secondary diagnosis' and 'comorbidities' returned no results.

## Hospital course
Hospital course summary: The patient was admitted for an acute heart failure exacerbation. The patient reported feeling much better and less short of breath. On examination, there were crackles at the lung bases and 1+ pedal edema, both of which had improved since admission.

_Sources: p.2_

## Procedures  —  **MISSING**
> Search for 'procedure' returned no results.

## Admission medications
- name_as_written=Lisinopril, dose=20 mg, route=by mouth, frequency=daily
- name_as_written=Metoprolol Succinate, dose=100 mg, route=by mouth, frequency=daily
- name_as_written=Furosemide, dose=40 mg, route=by mouth, frequency=twice a day
- name_as_written=Spironolactone, dose=25 mg, route=by mouth, frequency=daily
- name_as_written=Atorvastatin, dose=40 mg, route=by mouth, frequency=daily
- name_as_written=Aspirin, dose=81 mg, route=by mouth, frequency=daily

_Sources: p.5, p.5, p.5, p.5, p.5, p.5_

## Discharge medications
- name_as_written=Furosemide, dose=40 mg, route=by mouth, frequency=daily
- name_as_written=Lisinopril, dose=20 mg, route=by mouth, frequency=daily
- name_as_written=Metoprolol Succinate, dose=100 mg, route=by mouth, frequency=daily
- name_as_written=Spironolactone, dose=25 mg, route=by mouth, frequency=daily
- name_as_written=Potassium Chloride, dose=20 mEq, route=by mouth, frequency=daily

_Sources: p.5_

## Medication changes  —  **FLAGGED**
> loop ended without commit — clinician must decide

## Drug interactions  —  **FLAGGED**
> loop ended without commit — clinician must decide

## Allergies  —  **FLAGGED**
> loop ended without commit — clinician must decide

## Follow-up instructions  —  **FLAGGED**
> loop ended without commit — clinician must decide

## Pending results  —  **FLAGGED**
> loop ended without commit — clinician must decide

## Discharge condition  —  **FLAGGED**
> loop ended without commit — clinician must decide

## Safety flags raised
- **[medium]** `medication_changes` — loop ended without commit — clinician must decide
- **[medium]** `allergies` — loop ended without commit — clinician must decide
- **[medium]** `follow_up` — loop ended without commit — clinician must decide
- **[medium]** `pending_results` — loop ended without commit — clinician must decide
- **[medium]** `discharge_condition` — loop ended without commit — clinician must decide
- **[medium]** `drug_interactions` — loop ended without commit — clinician must decide


---
*This is a DRAFT for clinician review. Sections marked PENDING, MISSING, FLAGGED, or CONFLICTING require explicit clinician action before finalizing.*