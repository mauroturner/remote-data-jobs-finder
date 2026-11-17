# your-resume

Drop **one** resume file here (PDF, DOCX, DOC, PPTX, HTML, TXT, RTF or ODT).

Then run:

```bash
python -m jobfinder.resume
```

It converts the most recently modified file in this folder to `resume.md` using
[Microsoft MarkItDown](https://github.com/microsoft/markitdown). Your agent reads `resume.md` to build your
search profile (see `AGENTS.md` at the repository root).

Everything in this folder except this README is git-ignored: your resume and `resume.md` never get committed.

Notes:

- Scanned PDFs (images without a text layer) produce no text. Export your resume as a text PDF or DOCX.
- Re-run the command whenever you update your resume.
