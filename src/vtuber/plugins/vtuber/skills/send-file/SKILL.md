---
name: send-file
description: Activate when you need to send files to the user. Triggers on file download requests, export requests, generated files that need delivery, or any scenario requiring local file transfer to the user.
---

# Sending Files to the User

When you need to send one or more local files to the user, **do not** paste file paths inline or describe where the file is located.

## Output Format

Output the file paths as a **pure JSON array** where each element is an **absolute path** string:

```
["/absolute/path/to/file1.txt", "/absolute/path/to/file2.pdf"]
```

## Rules

1. Your reply **must contain only** this JSON array — no additional text, explanation, or markdown formatting.
2. All paths must be **absolute** (starting with `/`).
3. Ensure the file already exists at the specified path.
4. Supported file types: `.pdf`, `.md`, `.markdown`, `.txt`, `.ppt`, `.pptx`, `.doc`, `.docx`, `.wav`, `.mp3`, `.jpg`, `.jpeg`, `.gif`, `.png`
5. Even for a single file, use the array format: `["/path/to/file.txt"]`

## Examples

User asks "send me the report" — after generating the report file, reply with:

```
["/home/agent/.vtuber/workspace/Downloads/report.pdf"]
```

User asks "send me those two images":

```
["/home/agent/.vtuber/workspace/Downloads/chart1.png", "/home/agent/.vtuber/workspace/Downloads/chart2.png"]
```
