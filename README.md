# weekly-health-ai-papers

A beginner-friendly GitHub project that automatically collects recent papers about **AI + health** every Friday and saves a Markdown report in the repository.

The collector focuses on four research areas:

1. **Clinical medicine**
   - Nature Medicine
   - NEJM
   - The Lancet
   - JAMA
   - BMJ
2. **Computer science**
   - Nature Machine Intelligence
   - Nature Computational Science
   - ICML
   - NeurIPS
   - AAAI
   - KDD
3. **Evidence-based medicine**
   - Cochrane
   - BMJ Evidence-Based Medicine
   - Annals of Internal Medicine
4. **Management science and engineering**
   - Management Science
   - MIS Quarterly
   - Information Systems Research

The project uses only free public sources:

- RSS feeds
- PubMed API
- arXiv API

It does **not** use the OpenAI API or any paid service.

## Output

Each run creates one Markdown file named:

```text
papers_YYYY_MM_DD.md
```

Example:

```text
papers_2026_06_05.md
```

Each paper entry contains:

- Title
- Journal / venue
- Authors
- Publication date
- Abstract
- Link

## Project structure

```text
weekly-health-ai-papers/
├── README.md
├── requirements.txt
├── collect_papers.py
├── config.yaml
└── .github/
    └── workflows/
        └── friday.yml
```

## Quick start

### 1. Install Python dependencies

Use Python 3.10 or newer.

```bash
pip install -r requirements.txt
```

### 2. Run the collector locally

```bash
python collect_papers.py
```

This collects papers from the last 7 days and writes a report in the current folder.

### 3. Change keywords, journals, or feeds

Edit `config.yaml`.

Important sections:

- `keywords`: search terms used to keep relevant papers
- `pubmed.journals`: journals searched through PubMed
- `rss_feeds`: RSS sources searched directly
- `arxiv.categories`: arXiv categories searched through the arXiv API

## GitHub Actions automation

The workflow in `.github/workflows/friday.yml` runs automatically every Friday.

It will:

1. Check out the repository
2. Install Python dependencies
3. Run `collect_papers.py`
4. Commit the generated `papers_YYYY_MM_DD.md` report back to the repository if there are changes

You can also run it manually from the GitHub Actions page because the workflow includes `workflow_dispatch`.

## Notes for beginners

- The code is intentionally written in one Python file so it is easy to inspect.
- The configuration is separated into `config.yaml` so you can change search terms without changing Python code.
- Public APIs and RSS feeds can occasionally be unavailable or return incomplete metadata. When an abstract is unavailable, the report shows `No abstract available.`
- Conference papers such as ICML, NeurIPS, AAAI, and KDD are often indexed through arXiv before formal proceedings metadata is available, so this project searches arXiv computer science categories using the configured keywords.

## Manual examples

Collect papers from the last 14 days:

```bash
python collect_papers.py --days 14
```

Use a different configuration file:

```bash
python collect_papers.py --config config.yaml
```

Write reports to a different folder:

```bash
python collect_papers.py --output-dir reports
```
