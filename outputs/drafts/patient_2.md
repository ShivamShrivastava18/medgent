# Discharge Summary Draft

_Generated 2026-06-03T04:35:37.432624+00:00_  ·  source: `data/patients/patient_2/source.pdf`

**Agent metrics:** 42 iterations, 26 tool calls

**Safety flags:** 8

## Patient demographics  —  **FLAGGED**
> Patient demographic information (e.g., name, MRN, DOB) could not be located in the provided source notes after multiple search attempts.

## Admission date  —  **FLAGGED**
> all supporting citations are low-confidence handwritten; downgraded FILLED → FLAGGED by handwriting floor

## Discharge date
2026-03-09

_Sources: p.2_

## Principal diagnosis
Acute gastroenteritis with dehydration

_Sources: p.1_

## Secondary diagnoses
- Urinary Tract Infection

_Sources: p.1_

## Hospital course  —  **FLAGGED**
> Could not find a summary of the patient's hospital course in the available documents after multiple search attempts.

## Procedures  —  **FLAGGED**
> No information regarding procedures performed during the hospital stay was found in the available documents after multiple search attempts.

## Admission medications  —  **FLAGGED**
> Could not find a list of the patient's admission or home medications after using both the get_medications tool and a keyword search.

## Discharge medications
- name_as_written=RACIPER, dose=40MG, route=PO, frequency=1-0-0, duration=7 DAYS
- name_as_written=ULTRACET, route=PO, frequency=1-0-1, duration=3 DAYS
- name_as_written=ZUCOL, dose=10MG, route=PO, frequency=0-0-1, duration=15 DAYS
- name_as_written=PAN, dose=40MG, route=PO, frequency=1-0-0, duration=15 DAYS
- name_as_written=CLAVAM, dose=625MG, route=PO, frequency=1-1-1, duration=5 DAYS
- name_as_written=DOLO, dose=650MG, route=PO, frequency=SOS
- name_as_written=CREMAFFIN, dose=3TSP, route=PO, frequency=HS, duration=5 DAYS
- name_as_written=A TO Z, route=PO, frequency=1-0-0, duration=15 DAYS

_Sources: p.2_

## Medication changes
- medication_name=loperamide, change_type=MedChangeType.ADDED, needs_reconciliation=False, citations=[]
- medication_name=metronidazole, change_type=MedChangeType.ADDED, needs_reconciliation=False, citations=[]
- medication_name=ondansetron, change_type=MedChangeType.ADDED, needs_reconciliation=False, citations=[]
- medication_name=dicyclomine, change_type=MedChangeType.ADDED, needs_reconciliation=False, citations=[]
- medication_name=ULTRACET, change_type=MedChangeType.ADDED, needs_reconciliation=False, citations=[]
- medication_name=ORS, change_type=MedChangeType.ADDED, needs_reconciliation=False, citations=[]
- medication_name=RACIPER, change_type=MedChangeType.ADDED, needs_reconciliation=False, citations=[]

_Sources: p.2_

## Drug interactions

_Sources: p.2_

## Allergies  —  **FLAGGED**
> No information regarding patient allergies was found in the record after a keyword search.

## Follow-up instructions  —  **FLAGGED**
> No information regarding follow-up instructions was found in the record after multiple keyword searches.

## Pending results  —  **MISSING**

## Discharge condition  —  **FLAGGED**
> No information regarding the patient's condition at the time of discharge was found in the record after multiple keyword searches.

## Safety flags raised
- **[high]** `demographics` — Patient demographic information (e.g., name, MRN, DOB) could not be located in the provided source notes after multiple search attempts.
- **[medium]** `admission_date` — all supporting citations are low-confidence handwritten; downgraded FILLED → FLAGGED by handwriting floor
- **[medium]** `hospital_course` — Could not find a summary of the patient's hospital course in the available documents after multiple search attempts.
- **[medium]** `procedures` — No information regarding procedures performed during the hospital stay was found in the available documents after multiple search attempts.
- **[medium]** `admission_medications` — Could not find a list of the patient's admission or home medications after using both the get_medications tool and a keyword search.
- **[medium]** `allergies` — No information regarding patient allergies was found in the record after a keyword search.
- **[medium]** `follow_up` — No information regarding follow-up instructions was found in the record after multiple keyword searches.
- **[medium]** `discharge_condition` — No information regarding the patient's condition at the time of discharge was found in the record after multiple keyword searches.


---
*This is a DRAFT for clinician review. Sections marked PENDING, MISSING, FLAGGED, or CONFLICTING require explicit clinician action before finalizing.*