# Security

hey-mac controls your Mac and holds an API key, so security reports matter.

**Please don't open a public issue.** Use GitHub's
[private vulnerability reporting](https://github.com/tinexu/hey-mac/security/advisories/new) instead.
I'll reply as soon as I can.

Things that are in scope:
- A way to make the assistant run commands outside its fixed tool list (for example, prompt injection through a
  file name, web page or note that leads to an unintended action)
- Anything that could leak your API key, files, messages or notes

Your API key (Anthropic, OpenAI or Gemini) lives in `.env`, which is git-ignored. Never paste it into an issue or a log.