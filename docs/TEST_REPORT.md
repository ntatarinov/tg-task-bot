# Test report

All results in this report were measured with Russian model output; the English output mode, the default since 2026-11-05, has not been evaluated on a real model yet.

Date: 2026-11-04. Checks ran on synthetic data and on a copy of the database. The contents of `bot.db` were not changed;
running `app.doctor` switched its journal to WAL mode, which did not affect the data.

## Summary

| Check | Result |
|---|---|
| Automated tests (`pytest`) | 774 passed, 2 skipped |
| Code coverage | 98% |
| `ruff`, `mypy` | Clean |
| CI checks from a clean copy without `.env` | Passed |
| Real bot process and fake Telegram API | 19 of 19, including scheduled digests |
| Real panel process | Login, settings and restart work; the token does not reach responses or logs |
| Move archive | 258 tests passed from the unpacked copy (at the time of the check) |
| Schema update on a copy of `bot.db` | 2 duplicates removed, links intact, `integrity_check: ok` |

Skipped tests: the `install.sh` syntax check (PowerShell has no working `bash`; it passes manually through Git Bash)
and one case with a model name.

### What was not verified

- **Live Telegram.** The bot has never connected to real Telegram with the new token.
- **Real work conversations.** Quality was measured only on synthetic data.
- **Docker.** The daemon was off: the image was not built, and `install.sh` and the containers were not run. Verified: the format of the compose files, the script syntax and the `pip install` step.
- **Postgres.** The code is written for both dialects but was run only on SQLite.
- **Panel in a browser.** The API and script syntax were checked, but the pages were not opened in a real browser.

## Scenarios from the assignment

`pytest` with a predefined model response checks the code on all scenarios. The live run checks comprehension.

| # | Scenario | Expected | Code check |
|---|---|---|---|
| 1 | Russian only | Decision, task with assignee and deadline | Passed |
| 2 | English only | Same | Passed |
| 3 | Mixed conversation | One topic, decision, task | Passed |
| 4 | One topic, several participants | One topic, decision | Passed |
| 5 | Several topics at once | Topics not mixed | Passed |
| 6 | Return to an old topic | Topic continued | Passed |
| 7 | Proposal without a decision | No decisions, proposal stored | Passed |
| 8 | Decision changed | New one applies, old one replaced | Passed |
| 9 | Task for a specific person | Assignee and deadline | Passed |
| 10 | Task without an owner | Assignee and deadline not invented | Passed |
| 11 | Large conversation | 300 and 3000 messages, request size does not grow | Passed |
| 12 | Model or Telegram failure | Nothing lost, sending resumes | Passed |
| 13 | Repeated processing | One record, one analysis | Passed |
| 14 | Scheduled digest | Once; a missed one is caught up | Passed |

Additional scenarios: injection, personal conversation, closure with evidence, repeated complaint, change of assignee,
deadline moved, task cancelled, short confirmation, same task in two projects, English deadline with a mention, jargon.

## Models

Final run: final code and prompt, 21 short scenarios twice and a 300-message conversation three times.

| Model | Short scenarios | 300 messages | Where it runs |
|---|---|---|---|
| **`gemma4:26b`** (selected) | 41 of 42 | **3 of 3** | Local, 18 GB |
| `gemma4:12b` | 42 of 42 | 2 of 3: one run lost a decision | Local, 8 GB |
| `openai/gpt-oss-120b` | 21 of 21 | Not completed on the final prompt because of the Groq limit (429); 3 of 3 on the previous set | Cloud only: needs 80 GB of video memory |
| `qwen/qwen3.8-27b` | 23 of 23 on the previous set | 0 of 4: loses important single messages | Not recommended |
| `openai/gpt-oss-20b` | Not run | Not run | Fell for the injection |

The only `gemma4:26b` failure: in one of two runs of the "deadline moved" scenario, the new deadline was not recorded.
The sample is small; this is a basis for the choice, not a guarantee. `gpt-oss-120b` was run once, and the large scenario on the final prompt was cut off by the request limit, so there is no quality assessment for it.

Speed of local `gemma4:26b` with `reasoning_effort: none` on an RTX 3070 8 GB and 32 GB RAM: about 10 seconds per short scenario,
about 3.5 minutes for 300 messages. Without disabling reasoning, the model is 5 times slower and truncates responses.

Groq key limits: 1000 requests per day, 8000 tokens per minute. Frequent requests trigger error 429; the bot
keeps messages in the queue until the next tick.

## Bugs found during testing and fixed

| Bug | Found by |
|---|---|
| A changed decision was merged with the old one: «релиз 10 ноября» ("release on November 10") and «релиз 17 ноября» ("release on November 17") | `pytest` |
| The model closed live blockers without grounds, and they disappeared from the report | Review of model results |
| Closing an old decision without creating a new one lost the current date | Live run |
| A real decision was downgraded to a proposal if the model did not attach a quote | Live run |
| Statuses like «логи в порядке» ("logs are fine") were recorded as decisions | Live run |
| The local model truncated responses because of reasoning | Live run |
| `content_type` was stored as `ContentType.TEXT` | Handler tests |
| The message edit time was lost | Handler tests |
| A broken time zone stopped a chat's analysis and reports | `pytest` |
| A failure in one chat stopped processing of the others | Code review, then a test |
| A burst of messages from a new user violated uniqueness | `pytest` |
| The mock model always returned an empty result | `pytest` |
| `install.sh` with Windows line endings would not have run on Linux | `pytest` |
| Every restart would have created a full copy of the database | Code review, then a test |
| The Docker image did not build | Reproducing the build step |
| `.env` and `bot.db` would have ended up in the Docker image | Review of the `Dockerfile` |
| Panel edits would not have been applied in Docker | Configuration review |

Bugs in the checks themselves were fixed too: an empty assertion in the Redis test, a connection test that passed because of the sending
ban, and the scenario 8 check that treated the phrase «10 ноября отменяем» ("November 10 is cancelled") in the new decision as a failure.

## Known limitations

- Injection protection recognizes typical wording. An unusual injection will reach the model; the instruction in the prompt and the code checks remain as protection.
- Decision confirmation is detected by words («решили» (decided), «договорились» (agreed), «подтверждаю» (I confirm) and others). A decision in other words becomes a proposal: this is deliberately the safe side of the error.
- If the database is unavailable for more than three attempts, the message is lost and only a log entry remains.
- The bot cannot see message deletion in Telegram.

## How to reproduce

```powershell
.venv\Scripts\python.exe -m pytest --cov=app
.venv\Scripts\python.exe scripts\e2e_fake_telegram.py
.venv\Scripts\python.exe scripts\eval_llm.py --model gemma4:26b --runs 2 --large 3 --pause 0 --reasoning none --max-tokens 6000
```
