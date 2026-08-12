# Contributing to ArchAgent

Thanks for considering a contribution. This is a small project, so the process is light.

## Ground rules that shape every change

Three constraints define ArchAgent. A change that breaks one of them will be asked to change,
however good it is otherwise:

1. **Zero runtime dependencies.** Standard library only. `requirements.txt` stays empty. If a
   feature seems to need a package, it either doesn't ship or it gets written in stdlib.
2. **No data in the repository.** Databases, exports, backups, scraped records, and real tender
   documents are never committed. Copy `.gitignore.example` to `.gitignore` before your first
   commit — `.gitignore` itself is intentionally untracked.
3. **Legal logic must cite its source.** Anything encoding D.Lgs 36/2023 (or any other rule)
   carries the article reference in the code and in its output. Uncited rules are not merged.

## Development setup

```bash
git clone https://github.com/MSKazemi/archagent.git && cd archagent
cp .gitignore.example .gitignore
cp .env.example .env
python3 archagent_server.py --host 127.0.0.1 --port 8091
```

Nothing to install. Python 3.9+ is the floor and CI enforces it.

## Running the tests

Every test script is self-contained and prints one `PASS ...` line. Run the full set before
opening a pull request:

```bash
python3 tests/test_db.py
python3 tests/test_procurement.py
python3 tests/test_maintenance.py
python3 tests/test_analyst.py
python3 tests/test_admin.py
python3 tests/test_admin_data.py
python3 tests/test_smoke.py        # spawns a server on port 8092
python3 tests/test_regression.py
python3 tests/test_italy.py
python3 ops/maintenance.py
```

`test_smoke`, `test_regression`, and `test_italy` bind a local port; some sandboxes block that.
CI runs all of them on Python 3.9, 3.11, and 3.13.

## Making a change

1. Branch off `main`.
2. Write the code **and its test** in the same change. New domain rules need a test asserting
   the numbers, not just that the function runs.
3. Match the surrounding style — the codebase uses plain functions, explicit SQL, and short
   module-level docstrings. No frameworks, no metaclasses, no clever abstractions.
4. Update the docs the change touches: `README.md`, `CHANGELOG.md`, `docs/`, and `CLAUDE.md`
   if you alter architecture.
5. Open a pull request describing what changed and why, with the test output pasted in.

## What is especially welcome

- **Additional national procurement rulesets** — the Italian module is the template; the same
  shape would work for other member states.
- **ANAC / BDNCP integration** for live CIG lookup and award history (see `ROADMAP.md`).
- **Corrections to the legal encoding**, with the article citation. These are the most valuable
  contributions and will be merged fastest.
- **Test coverage** for paths that currently have none.
- Bug reports with a reproduction.

## What is out of scope

- Adding a web framework, ORM, or any runtime dependency.
- Committing sample databases or real tender documents.
- Features that present unverified OpenStreetMap listings as vetted partners.
- Anything that would make a generated document look like a final, review-free deliverable.

## Reporting bugs and vulnerabilities

Ordinary bugs → open an issue with the reproduction and your Python version.
Security vulnerabilities → **do not** open a public issue; follow [SECURITY.md](SECURITY.md).

## Licensing of contributions

By contributing you agree that your contribution is licensed under the Apache License 2.0, the
same as the project. You retain copyright in your contribution.
