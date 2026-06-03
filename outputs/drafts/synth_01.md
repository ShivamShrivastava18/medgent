# Discharge Summary Draft

_Generated 2026-06-03T11:01:31.683745+00:00_  ·  source: `data/synthetic/synth_01/source.pdf`

**Agent metrics:** 43 iterations, 27 tool calls

**Safety flags:** 4

## Patient demographics
- **name:** synth_01

_Sources: p.1_

## Admission date
2024-08-12

_Sources: p.1_

## Discharge date
2024-08-14

_Sources: p.3_

## Principal diagnosis
Dengue fever with thrombocytopenia

_Sources: p.1_

## Secondary diagnoses  —  **MISSING**
> agent did not commit a value

## Hospital course
Hospital course summary: The patient was admitted for Dengue fever with thrombocytopenia, presenting with fever, myalgia, and headache. The patient received intravenous fluids and supportive care. The patient responded well to treatment and was stable for discharge.

_Sources: p.1, p.2, p.3, p.6_

## Procedures  —  **MISSING**
> agent did not commit a value

## Admission medications
- name_as_written=RINGER'S LACTATE, dose=1 pint, route=IV, frequency=@ 125 ml/hr
- name_as_written=PARACETAMOL, dose=500 mg, route=PO, frequency=Q6H
- name_as_written=Ondansetron, dose=4 mg, route=IV, frequency=SOS
- name_as_written=Pantoprazole, dose=40 mg, route=IV, frequency=OD

_Sources: p.5_

## Discharge medications
- name_as_written=DOLO-650, dose=650mg, frequency=SOS, duration=5 days
- name_as_written=PANTOPRAZOLE, dose=40mg, frequency=OD, duration=5 days
- name_as_written=ONDANSETRON, dose=4mg, frequency=SOS, duration=5 days
- name_as_written=VITAMIN C, dose=500mg, frequency=OD, duration=5 days
- name_as_written=CARIPAPIL, dose=1 tab, frequency=TID, duration=5 days
- name_as_written=LIV-52, dose=2 tsp, frequency=BID, duration=5 days

_Sources: p.5_

## Medication changes  —  **FLAGGED**
> agent attempted FILLED but citations invalid (citation excerpt does not overlap page 5 content; citation excerpt does not overlap page 4 content; citation excerpt does not overlap page 5 content); downgraded to FLAGGED by guardrail

## Drug interactions  —  **FLAGGED**
> Automated check found no interactions, and no interactions were documented in the chart. This negative finding cannot be cited from a source document as required for a 'filled' status.

## Allergies
- No known drug allergies

_Sources: p.1_

## Follow-up instructions
['Follow up with your primary care physician in 5-7 days']

_Sources: p.5_

## Pending results  —  **FLAGGED**
> agent attempted FILLED but citations invalid (citation excerpt does not overlap page 1 content); downgraded to FLAGGED by guardrail

## Discharge condition
Stable, afebrile, with improved platelet count.

_Sources: p.6_

## Safety flags raised
- **[medium]** `medication_changes` — agent attempted FILLED but citations invalid (citation excerpt does not overlap page 5 content; citation excerpt does not overlap page 4 content; citation excerpt does not overlap page 5 content); downgraded to FLAGGED by guardrail
- **[medium]** `pending_results` — agent attempted FILLED but citations invalid (citation excerpt does not overlap page 1 content); downgraded to FLAGGED by guardrail
- **[low]** `drug_interactions` — Automated check found no interactions, and no interactions were documented in the chart. This negative finding cannot be cited from a source document as required for a 'filled' status.
- **[medium]** `hospital_course` — verifier stripped unsupported sentence: During the hospital stay, the patient's fever subsided and the platelet count increased from 80,000 to 110,000. — verifier call failed; conservative default


---
*This is a DRAFT for clinician review. Sections marked PENDING, MISSING, FLAGGED, or CONFLICTING require explicit clinician action before finalizing.*