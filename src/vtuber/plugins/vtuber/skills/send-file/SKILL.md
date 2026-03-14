---
name: send-file
description: Send files to the user. Use this skill whenever the user asks to send, transfer, export, or deliver any file to them — regardless of whether they say "send file", "give me the file", "I need that document", or any similar request.
---

# File Transfer to User

When the user needs to receive a local file, use the vtuber file transfer system to deliver it.

## How It Works

The vtuber system intercepts **pure JSON arrays** containing file paths and automatically transfers those files to the user. This is a system-level integration — the JSON output is parsed and handled specially, not displayed as regular text.

## Critical Requirement

**Your reply must contain ONLY the raw JSON array** — no markdown formatting, no explanatory text, no code blocks, no additional characters whatsoever.

The system expects raw JSON like this:
["/path/to/file.txt"]

NOT this:
```
["/path/to/file.txt"]
```

NOT this:
```
Here's your file: ["/path/to/file.txt"]
```

## Output Format

Output file paths as a **pure JSON array** with **absolute paths**:

**Single file:**
["/home/agent/.vtuber/workspace/report.pdf"]

**Multiple files:**
["/home/agent/.vtuber/workspace/chart1.png", "/home/agent/.vtuber/workspace/chart2.png", "/home/agent/.vtuber/workspace/data.csv"]

## Requirements

1. **Pure JSON only** — No markdown, no text, no code blocks, no explanations
2. **Absolute paths only** — Must start with `/`
3. **Verify files exist** — Ensure file is at the specified path before outputting
4. **Always use array format** — Even for single files: `["/path"]`
5. **Supported file types** — `.pdf`, `.md`, `.markdown`, `.txt`, `.ppt`, `.pptx`, `.doc`, `.docx`, `.wav`, `.mp3`, `.jpg`, `.jpeg`, `.gif`, `.png`, and many others

## Examples

User: "Send me the report"
Reply: ["/home/agent/.vtuber/workspace/Downloads/report.pdf"]

User: "I need those two charts"
Reply: ["/home/agent/.vtuber/workspace/Downloads/chart1.png", "/home/agent/.vtuber/workspace/Downloads/chart2.png"]

User: "Can you download and send me the data?"
(You download the file to `/home/agent/.vtuber/workspace/Downloads/data.csv`), then output:
["/home/agent/.vtuber/workspace/Downloads/data.csv"]


Any extra text (including markdown code blocks) breaks this detection and the files won't transfer properly.
