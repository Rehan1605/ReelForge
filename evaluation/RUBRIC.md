# InstaBrain Evaluation Rubric — v1.0

## 1. Purpose

Evaluate whether InstaBrain correctly transforms an Instagram Reel into structured, useful knowledge.

Every evaluation must compare:

**Source Evidence**

* Instagram caption
* Reel transcript

against:

**InstaBrain Output**

* category
* title
* summary
* extracted fields
* websites
* tags
* other structured knowledge

The evaluator must judge the output based on what is actually supported by the source.

---

# 2. Core Evaluation Principles

### Evidence First

A generated field is correct only if it is supported by the caption or transcript.

Do not reward information merely because it sounds plausible.

### No Hallucination

The system must not invent:

* facts
* numbers
* ingredients
* instructions
* URLs
* camera settings
* exercises
* costs
* tools
* movie names
* destinations
* financial claims
* prompts
* steps

If the source does not provide the information, the output should leave it empty rather than guess.

### Missing Information Is Different From Hallucination

These are separate failures.

Example:

Source:

> "Use ISO 800."

Output:

> ISO: 1600

→ **Hallucination / incorrect information**

Output:

> ISO: [missing]

→ **Missing information**

Output:

> ISO: 800

→ **Correct**

This distinction must be preserved in every evaluation.

### Category-Aware Evaluation

Do not penalize a category for fields that are irrelevant to that category.

For example, a Food reel should not be penalized because it has no camera settings.

---

# 3. Evaluation Dimensions

Every reel receives scores for the following dimensions.

## A. Category Accuracy — 0 to 5

Does the assigned category correctly represent the main subject of the reel?

| Score | Meaning                      |
| ----- | ---------------------------- |
| 5     | Clearly correct              |
| 4     | Correct with minor ambiguity |
| 3     | Reasonable but debatable     |
| 2     | Probably incorrect           |
| 1     | Clearly incorrect            |
| 0     | Completely unrelated         |

The category should represent what the user would want to remember the reel for.

---

# 4. Factual Accuracy — 0 to 5

Are the claims and extracted facts supported by the source?

| Score | Meaning                               |
| ----- | ------------------------------------- |
| 5     | All meaningful claims supported       |
| 4     | Very minor inaccuracies               |
| 3     | Some inaccurate/uncertain information |
| 2     | Multiple significant inaccuracies     |
| 1     | Mostly inaccurate                     |
| 0     | Fundamentally fabricated              |

Judges must identify the specific unsupported or incorrect claims.

---

# 5. Hallucination — 0 to 5

This measures whether InstaBrain invented information.

Here:

**5 = no hallucination**

| Score | Meaning                                |
| ----- | -------------------------------------- |
| 5     | No hallucinated information            |
| 4     | Extremely minor questionable inference |
| 3     | One noticeable unsupported claim       |
| 2     | Multiple unsupported claims            |
| 1     | Significant fabrication                |
| 0     | Output is predominantly fabricated     |

A plausible inference is not automatically a hallucination.

However, the judge must distinguish:

**Reasonable inference**

from

**unsupported invention**.

---

# 6. Completeness — 0 to 5

Did InstaBrain capture the important information actually present in the reel?

| Score | Meaning                                        |
| ----- | ---------------------------------------------- |
| 5     | Important information comprehensively captured |
| 4     | Minor information missing                      |
| 3     | Some useful information missing                |
| 2     | Significant information missing                |
| 1     | Most important information missing             |
| 0     | Almost nothing useful extracted                |

Do NOT require the system to copy everything.

The goal is useful knowledge, not transcript reproduction.

---

# 7. Relevance — 0 to 5

Does the output focus on information that is actually useful for remembering the reel?

| Score | Meaning                             |
| ----- | ----------------------------------- |
| 5     | Highly relevant and useful          |
| 4     | Mostly relevant                     |
| 3     | Mixed relevance                     |
| 2     | Considerable irrelevant information |
| 1     | Mostly irrelevant                   |
| 0     | Essentially useless                 |

Repeated transcript text should not receive a high score simply because it is factually correct.

---

# 8. Summary Quality — 0 to 5

Does the summary accurately communicate the main idea of the reel?

| Score | Meaning                                       |
| ----- | --------------------------------------------- |
| 5     | Accurate, concise, and captures the core idea |
| 4     | Good summary with minor omission              |
| 3     | Understandable but incomplete                 |
| 2     | Misleading or substantially incomplete        |
| 1     | Barely represents the reel                    |
| 0     | Incorrect or unrelated                        |

The summary should not introduce facts absent from the source.

---

# 9. Structured Field Accuracy — 0 to 5

Evaluate category-specific fields individually.

Examples:

### Food

* ingredients
* quantities
* steps
* cooking time
* preparation time
* servings
* tips

### Photography

* ISO
* shutter speed
* aperture
* focal length
* equipment
* techniques

### Gym

* exercises
* sets
* reps
* duration
* muscle groups
* instructions

### Travel

* destinations
* locations
* activities
* itinerary
* costs
* travel tips

### Finance

* concepts
* numbers
* examples
* financial claims
* warnings

### AI

* concepts
* tools
* prompts
* workflows
* techniques

### Programming

* concepts
* code-related knowledge
* tools
* commands
* techniques
* examples

### Movies & Edits

* movies
* editing techniques
* software
* steps
* filmmaking concepts

### Productivity

* techniques
* workflows
* habits
* tools
* steps

### Other

* key points
* steps
* recommendations
* useful information

The judge should evaluate fields based on whether they are:

1. Correct
2. Supported
3. Complete enough to be useful
4. Appropriate for the category

---

# 10. Website / URL Accuracy — 0 to 5

Evaluate websites separately.

A website should be:

* actually mentioned in the source, or
* explicitly provided as part of the reel's source information

Do not reward invented URLs.

| Score | Meaning                         |
| ----- | ------------------------------- |
| 5     | All URLs accurate and supported |
| 4     | Minor issue                     |
| 3     | Some uncertainty                |
| 2     | One or more questionable URLs   |
| 1     | Mostly incorrect/invented       |
| 0     | URLs are fabricated             |

If the reel contains no website information and the output contains no websites:

**This is correct.**

It should not be penalized for having an empty website field.

---

# 11. Tags — 0 to 5

Tags should accurately represent the extracted knowledge.

Good tags:

* specific
* relevant
* useful for retrieval
* supported by the source

Bad tags:

* generic filler
* unrelated concepts
* invented topics
* excessive duplication

An empty tag list is acceptable when the source provides insufficient information.

---

# 12. Extraction Integrity

Every field should be classified internally by the judge as one of:

```text
CORRECT
MISSING
INCORRECT
HALLUCINATED
IRRELEVANT
NOT_APPLICABLE
```

This is important because a single overall score cannot explain *why* something failed.

Example:

```json
{
  "field": "iso",
  "status": "HALLUCINATED",
  "expected": "ISO 800",
  "actual": "ISO 1600",
  "evidence": "Transcript states ISO 800."
}
```

---

# 13. Critical Errors

Some failures are more serious than others.

Mark `critical_error: true` if the output contains:

* fabricated financial information
* fabricated medical/health claims
* fabricated costs/prices
* fabricated URLs
* fabricated instructions that could materially change the recommended procedure
* completely incorrect category
* major factual contradiction
* substantial invented information
* a confident claim directly contradicted by the source

A reel can have a good overall score while still containing a critical error.

Critical errors must therefore be reported separately.

---

# 14. Overall Score

Calculate the overall score using:

```text
Category Accuracy     15%
Factual Accuracy      25%
Hallucination         20%
Completeness          15%
Relevance             10%
Summary Quality       10%
Structured Quality     5%
```

Website and tag quality should be reported separately and can contribute to structured quality where applicable.

The weighted score is converted to **0–100**.

---

# 15. Quality Classification

Use the following final classification:

| Score  | Classification    |
| ------ | ----------------- |
| 90–100 | Excellent         |
| 80–89  | Good              |
| 70–79  | Acceptable        |
| 60–69  | Needs Improvement |
| <60    | Poor              |

However:

**Any critical hallucination should prevent an "Excellent" classification**, regardless of numerical score.

---

# 16. Judge Output Contract

Every judge must return structured JSON.

Required structure:

```json
{
  "reel_id": "",
  "category_evaluation": {
    "expected": "",
    "actual": "",
    "correct": true,
    "score": 5
  },

  "scores": {
    "factual_accuracy": 0,
    "hallucination": 0,
    "completeness": 0,
    "relevance": 0,
    "summary_quality": 0,
    "structured_quality": 0,
    "website_accuracy": 0,
    "tag_quality": 0
  },

  "overall_score": 0,

  "critical_error": false,

  "issues": [
    {
      "field": "",
      "status": "CORRECT",
      "description": "",
      "evidence": ""
    }
  ],

  "missing_information": [],

  "hallucinations": [],

  "strengths": [],

  "final_verdict": ""
}
```

---

# 17. Judge Procedure

The judge must follow this process:

### Step 1 — Understand the source

Read the caption and transcript.

Determine:

* main topic
* important facts
* instructions
* numbers
* names
* URLs
* category
* key pieces of knowledge

### Step 2 — Understand the InstaBrain output

Read the generated Brain Object completely.

### Step 3 — Compare every meaningful claim

For each important extracted claim:

```text
Is it supported by the source?
```

If yes:

```text
CORRECT
```

If information was present but omitted:

```text
MISSING
```

If the output contradicts the source:

```text
INCORRECT
```

If the output introduces unsupported information:

```text
HALLUCINATED
```

If a field does not apply:

```text
NOT_APPLICABLE
```

### Step 4 — Evaluate usefulness

Determine whether the resulting Brain Object would actually be useful to the user later.

### Step 5 — Score each dimension.

### Step 6 — Identify critical errors.

### Step 7 — Produce only the required JSON.

---

# 18. Important Judge Rules

The judge must NOT:

* assume missing information
* use outside knowledge to "correct" the reel
* penalize empty fields when the source contains no information
* reward plausible hallucinations
* penalize concise summaries for not containing every transcript detail
* treat paraphrasing as hallucination
* treat reasonable normalization as hallucination
* require identical wording
* judge based on personal preference
* modify the InstaBrain output
* rewrite the Brain Object

The judge's job is **evaluation, not correction**.

---

# 19. Multiple-Judge Protocol

When multiple LLM judges are introduced, each judge receives the same:

```text
Reel ID
Caption
Transcript
Expected Category
InstaBrain Output
Evaluation Rubric
```

Judges must evaluate independently.

They must NOT see the other judges' results before producing their own evaluation.

This prevents groupthink.

---

# 20. Consensus Rules

After all judges finish:

### Agreement

If judges broadly agree:

```text
Judge A → 91
Judge B → 89
Judge C → 93
```

Use the aggregated score.

### Disagreement

If scores differ substantially:

```text
Judge A → 94
Judge B → 91
Judge C → 57
```

flag the reel for human review.

### Critical Error Agreement

If multiple judges independently identify the same critical error, mark it as high-confidence.

### Single-Judge Critical Error

If only one judge identifies a critical error, do not automatically reject the output.

Flag it for human review.

---

# 21. Evaluation Philosophy

The objective is NOT:

> "Can an LLM generate something that sounds good?"

The objective is:

> "Can InstaBrain reliably preserve useful information from an Instagram Reel without inventing information?"

Therefore:

**Faithfulness > verbosity**

**Evidence > plausibility**

**Useful extraction > transcript copying**

**Correct empty fields > fabricated fields**

**Consistent structured knowledge > impressive-looking prose**

---

# 22. Success Criteria for InstaBrain v1

After evaluating the 20-reel test set, the target is:

```text
Category accuracy       ≥ 90%
Factual accuracy        ≥ 90%
Hallucination score     ≥ 95%
Completeness            ≥ 85%
Relevance               ≥ 90%
Summary quality         ≥ 90%
```

Additionally:

```text
Critical hallucinations = 0
```

The purpose of these thresholds is to identify weak categories and guide further prompt improvements, not to artificially inflate the evaluation score.
