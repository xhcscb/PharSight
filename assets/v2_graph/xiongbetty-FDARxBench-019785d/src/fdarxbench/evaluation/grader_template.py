FDA_GRADER_TEMPLATE = """
You are grading answers to questions about FDA-approved drug labels. First, I will give examples of each grade, and then you will grade a new example.

You will see:
- a QUESTION about a drug's label,
- a GOLD TARGET: the reference answer derived from the label, and
- a PREDICTED ANSWER: the model's response.

Your job is to decide whether the PREDICTED ANSWER is:
- CORRECT
- INCORRECT
- NOT_ATTEMPTED

and then output a single letter:
- A for CORRECT
- B for INCORRECT
- C for NOT_ATTEMPTED

## Core grading rules

Treat the GOLD TARGET as the reference truth for what should be said.

A PREDICTED ANSWER is CORRECT if:
- It contains all clinically important information in the GOLD TARGET that is relevant to the QUESTION.
- It does NOT contain any statements that contradict the GOLD TARGET.
- It does NOT introduce specific clinical recommendations that are unsupported or clearly wrong.
- Wording differences are fine (paraphrases, reordered points, different sentences) as long as the meaning matches.

A PREDICTED ANSWER is INCORRECT if:
- It contradicts the GOLD TARGET (different dose, frequency, indication, population, contraindication, etc.), OR
- It introduces specific clinical facts (dose, schedule, indication, contraindication, population, lab threshold, etc.) that are not supported by the GOLD TARGET and would influence clinical use, OR
- It omits one or more MAJOR clinically important elements required by the QUESTION (for example, missing a key dose adjustment, missing a required contraindication), OR
- The GOLD TARGET is a refusal/non-answer (e.g. “Information not found in label”) but the model still gives a specific clinical recommendation instead of refusing.

A PREDICTED ANSWER is NOT_ATTEMPTED if:
- It clearly does NOT provide the required information from the GOLD TARGET (e.g. “I don’t know”, “I cannot answer from the label”), AND
- It does NOT invent or contradict clinical facts in the GOLD TARGET.

## Domain-specific guidance

1. Doses, frequencies, durations, and numeric thresholds
   - If the GOLD TARGET gives a specific dose, frequency, duration, or lab threshold, the PREDICTED ANSWER must have the same key numbers to be CORRECT.
   - Small formatting changes (e.g., “5 mg once daily” vs. “once daily 5 mg”) are fine.
   - If the predicted numbers differ in a way that would change dosing or eligibility, grade as INCORRECT.
   - Vague statements like “take as directed on the label” are usually NOT_ATTEMPTED unless the GOLD TARGET itself is vague.

2. Indications and populations
   - If the GOLD TARGET specifies indications or special populations (e.g., “patients with eGFR < 45 mL/min/1.73 m²”, “pediatric patients 6–17 years”), leaving out a major constraint or population can make the answer INCORRECT.
   - Minor wording differences (e.g., “patients with moderate to severe renal impairment” when the GOLD TARGET explicitly defines that range) can still be CORRECT if they preserve the same meaning.

3. Contraindications and warnings
   - If the QUESTION asks about contraindications or major warnings, missing a key contraindication or warning from the GOLD TARGET should be graded as INCORRECT, not NOT_ATTEMPTED.
   - Adding a serious new contraindication or warning that is not in the GOLD TARGET is INCORRECT, even if it sounds medically plausible.

## Examples

### Example 1 (dose and population)
```
Question: "What is the recommended saxagliptin dose for patients with eGFR < 45 mL/min/1.73 m²?"
Gold target: "The recommended dosage of saxagliptin tablets is 2.5 mg orally once daily for patients with eGFR < 45 mL/min/1.73 m², including those with moderate or severe renal impairment or ESRD."
Predicted answer 1: "Give 2.5 mg saxagliptin once daily in patients with eGFR below 45. This includes patients with moderate or severe renal impairment and ESRD."
→ A: contains the key dose and population, no contradictions.
Predicted answer 2: "Use the standard 5 mg once daily dose regardless of renal function."
→ B: contradicts the GOLD TARGET on dose and population.
Predicted answer 3: "I’m not sure what dose to use in patients with reduced kidney function based on this label."
→ C: does not provide the needed information.
```

### Example 2 (contraindication)
```
Question: "In which patients are potassium citrate tablets contraindicated?"
Gold target: "Potassium citrate extended-release tablets are contraindicated in patients with hyperkalemia or conditions predisposing them to hyperkalemia, patients with GI obstruction or delayed gastric emptying, patients with peptic ulcer disease, patients with active urinary tract infection with certain stones, and patients with renal insufficiency."
Predicted answer 1: "They are contraindicated in patients with hyperkalemia or at risk of hyperkalemia, GI obstruction or delayed gastric emptying, peptic ulcer disease, active urinary tract infection with certain stones, and renal insufficiency."
→ A: contains the correct information.
Predicted answer 2: "They are contraindicated only in patients with a history of allergies to potassium."
→ B: omits almost all key contraindications and adds an unsupported one.
Predicted answer 3: "The label does not clearly specify in which patients they are contraindicated."
→ C: fails to use the GOLD TARGET, but does not contradict it
```

## Final instruction

Here is a new example. 
```
Question: {question}
Gold target: {target}
Predicted answer: {predicted_answer}
```

Grade the predicted answer of this new question as one of:
A: CORRECT
B: INCORRECT
C: NOT_ATTEMPTED

Respond in the following format, on a single line:
LETTER: short reason

Where LETTER is exactly one of A, B, or C, and "short reason" is 1–2 sentences explaining your choice.
Do not include any other text.
""".strip()
