# FDARxBench: Benchmarking Regulatory and Clinical Reasoning on FDA Generic Drug Assessment

This repository contains the code and data to reproduce the experiments from the paper [FDARxBench: Benchmarking Regulatory and Clinical Reasoning on FDA Generic Drug Assessment](https://arxiv.org/abs/2603.19539).

We introduce an expert-curated, real-world benchmark for evaluating document-grounded question answering (QA) motivated by generic drug assessment, using U.S. Food and Drug Administration (FDA) drug label documents. Drug labels contain rich but heterogeneous clinical and regulatory information, making accurate question answering difficult for current language models. In collaboration with FDA regulatory assessors, we construct a multi-stage pipeline for generating high-quality, expert-curated QA examples spanning **factual**, **multi-hop**, and **refusal** tasks, and design evaluation protocols to assess both open-book and closed-book reasoning. Experiments across proprietary and open-weight models reveal substantial gaps in factual grounding, long-context retrieval, and safe refusal behavior.

<p align="center">
<img src="overview.png" width="80%">
</p>

## Benchmark Overview

FDARxBench is built from 700 FDA prescription drug labels and contains 17,223 expert-curated QA examples:

| Task | Count | Description |
|------|-------|-------------|
| Factual | 9,888 | Answerable from a single label section |
| Multi-hop | 3,400 | Requires reasoning across two sections |
| Refusal | 3,935 | Unanswerable from the label; model should abstain |

The benchmark evaluates models under multiple evidence settings:
- **Closed-book** — no label access; tests parametric knowledge
- **Open-book (full label)** — full label text with passage markers; tests long-context comprehension, grounding, and citation
- **Open-book (oracle passages)** — gold passages provided; isolates reasoning from retrieval
- **Retrieval** — passage retrieval from chunked labels; tests evidence selection

## Dependencies

The code is written in Python. Dependencies:
- Python >= 3.9
- tqdm

## Installation

```bash
pip install -e .
```

## Datasets

- `data/qa/qa.jsonl` — full evaluation set
- `data/qa/qa_toy.jsonl` — small debug set
- `data/labels/labels.jsonl` — 700 drug labels with passage markers and chunked text

Each QA record contains `task` (factual / multihop / refusal), `question`, `answer`, `set_id`, `drug_name`, `context` (list of passage dicts with `doc_chunk_index`, `section_title`, `text`), and `qid`.

Each label record contains `set_id`, `drug_name`, `label_raw` (full text with `||PASSAGE_XXXX||` markers), and `chunks` (list of passage strings indexed by `doc_chunk_index`).

## Usage

FDARxBench scripts generate ready-to-use prompt JSONL that you feed to any LLM of your choice. The workflow has three steps:

1. **Prepare prompts** — generate prompt JSONL with FDARxBench
2. **Run inference** — send the prompts to your LLM and collect predictions
3. **Evaluate** — grade predictions with a judge LLM

### Step 1: Prepare Prompts

#### Inference (three modes)

```bash
# Closed-book (no label context; skips refusal questions)
bash scripts/prepare_prompts.sh --mode closed --dataset full

# Open-book full label (includes passage markers and refusal questions)
bash scripts/prepare_prompts.sh --mode open_full --dataset full

# Open-book gold passages (oracle setting; skips refusal questions)
bash scripts/prepare_prompts.sh --mode open_passages --dataset full
```

#### Retrieval corpus

```bash
bash scripts/prepare_retrieval.sh --dataset full
```

### Step 2: Run Inference (Your LLM)

FDARxBench is model-agnostic — **you supply your own LLM** for this step. Each line in the prompt JSONL from Step 1 contains a `system_prompt` and `user_prompt` that you send to your model.

See [`scripts/example_inference.py`](scripts/example_inference.py) for a ready-to-use inference loop with example OpenAI and Anthropic API calls. Edit the `call_llm` function with your model of choice, then run:

```bash
python scripts/example_inference.py \
  --prompts prompts_full_closed.jsonl \
  --out my_predictions.jsonl
```

The output predictions file must contain at minimum `qid`, `question`, `gold_answer`, and `prediction` per line.

### Step 3: Evaluate

Once you have a predictions file from Step 2, prepare grading prompts:

```bash
bash scripts/prepare_grading.sh --predictions my_predictions.jsonl
```

This produces `my_predictions_grading_prompts.jsonl`. Send each record's `grader_system_prompt` + `grader_user_prompt` to a judge LLM. Each prediction is graded as:
- **A (CORRECT)** — contains all clinically important information, no contradictions
- **B (INCORRECT)** — contradicts gold target, introduces unsupported facts, or omits major elements
- **C (NOT_ATTEMPTED)** — model abstains without introducing incorrect claims

## Citation

If you find this repository useful, or you use it in your research, please cite:

```bibtex
@misc{xiong2026fdarxbenchbenchmarkingregulatoryclinical,
      title={FDARxBench: Benchmarking Regulatory and Clinical Reasoning on FDA Generic Drug Assessment}, 
      author={Betty Xiong and Jillian Fisher and Benjamin Newman and Meng Hu and Shivangi Gupta and Yejin Choi and Lanyan Fang and Russ B Altman},
      year={2026},
      eprint={2603.19539},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2603.19539}, 
}
```

## Contact

For questions about the paper or issues with the repository, please email xiongb@stanford.edu.
