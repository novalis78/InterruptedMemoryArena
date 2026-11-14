# InterruptedMemoryArena

**A group trip, planned one traveler at a time, with people changing their minds in between. Does the agent remember, and does it update?**

InterruptedMemoryArena builds on the group travel planner from
[MemoryArena](https://huggingface.co/datasets/ZexueHe/memoryarena) and on the interruption taxonomy of
[InterruptBench](https://github.com/HenryPengZou/InterruptBench). In MemoryArena's group travel tasks, several travelers
plan a trip together. Each new traveler states their own wishes and often refers to an earlier traveler ("I'll have
dinner wherever Anna has dinner on day 2"), so the agent has to carry what it decided across the whole task.

Here every traveler is a separate session with a fresh conversation, and the agent keeps only what it wrote down on its
own machine between sessions. Then the travelers interrupt:

- **retract**: right after the agent answers, the traveler takes back one request in the same conversation.
- **revise**: in a later session, an earlier traveler now wants to join someone else for one meal or night.
- **add**: in a later session, an earlier traveler adds a request they forgot.
- **undo**: in a later session, an earlier traveler takes back a revision they made in an even earlier session.

Joins are live links: whoever joined a traveler for a slot follows that traveler's final choice, so one change can move
several plans. The ground truth is recomputed by replaying the events, and only slots whose dependents are all joins are
ever changed, so every new answer is exact.

Built to test agents on [bootkey.ai](https://bootkey.ai).

> **Status: work in progress.** This repository holds our own code only. **The task data is not included**, pending
> permission from the MemoryArena and InterruptBench authors, whom we have written to. No results are published yet.

## What is measured

| Measure | Meaning |
|---|---|
| `PS` | Traveler pass: every field of the traveler's final plan matches (MemoryArena's travel metric, fuzzy match at 0.7) |
| `SPS` | Constraint-slot rate: the slots where a traveler differs from the first traveler, averaged per chain |
| `SR` | Chain success: every traveler in the chain passes |
| `changed_updated` / `changed_stale` | Slots whose answer moved because of an interruption: updated, or still the old answer |
| `undo_restored` / `undo_stale` | Revisions that were taken back: restored, or the withdrawn revision kept |

`runner/analyze.py` adds measures that separate memory from planning: whether a plan that was right at the end of its
own session is still right at the end of the chain, the first session after which the submitted plans go wrong, and
whether every live join stays consistent with the agent's own answer for the traveler it follows.

## How a run works

1. `gen/make_arena.py` turns MemoryArena's group travel tasks into chains: one session per traveler, interruptions
   placed by a seeded generator, and the expected final plan for every traveler.
2. Each chain runs in a fresh home for one Linux user. Every session is a new conversation with the same agent, which
   keeps its files between sessions. The prompts are identical for every agent; only the harness differs.
3. The agent looks things up and submits plans with one command-line tool, `trip` (restaurants, hotels and attractions
   per city; `trip submit NAME FILE`), served by `server/trip_server.py`. The last plan submitted for a traveler counts.
4. Every model call goes through `server/llm_proxy.py`, which fixes the model, the provider and the reasoning effort for
   every agent, records token use and cost per turn, and retries rate-limited calls the same way for everyone, so no
   harness is scored on its own retry logic.
5. A network fence (`runner/deploy_server.sh`) lets the agent users reach only the trip server and the model proxy.
6. `runner/grade.py` scores each chain; `runner/summarize.py` and `runner/analyze.py` roll the scores up per agent and model.

## Agents in this release

| Arm | Agent |
|---|---|
| `hermes` | [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.23.1 (Nous Research), one install per agent slot |
| `picoclaw` | [PicoClaw](https://github.com/sipeed/picoclaw) v0.3.1 (Sipeed) |

Our private runs also include two arms that are not part of this release: Hermes with bootkey's coding hands, and
Mannie, our proprietary bootkey.ai agent.

## Layout

```
gen/       make_arena.py (chains and ground truth), deps.py (who follows whom, per slot)
server/    trip_server.py + trip (travel tools and the plan inbox), llm_proxy.py (model proxy)
runner/    setup_box.sh, install_hermes_slots.sh, deploy_server.sh (one Linux box, eight agent slots),
           arms.py (how each agent is set up and called), orchestrate.py (runs chains), grade.py, regrade.py,
           summarize.py, analyze.py, files.py
```

## Running it

You need MemoryArena's group travel planner data (CC BY 4.0) from its
[dataset page](https://huggingface.co/datasets/ZexueHe/memoryarena), an Ubuntu machine you can give to the benchmark,
and an OpenRouter key.

```
python3 gen/make_arena.py group_travel_planner.jsonl arena.json      # build the chains
sudo bash runner/setup_box.sh                                        # agents and users
sudo bash runner/install_hermes_slots.sh                             # one Hermes per slot
sudo bash runner/deploy_server.sh                                    # trip server, proxy, fence (see the script for its inputs)
sudo python3 runner/orchestrate.py jobs.json --slots 8               # run; results in /var/lib/ima/runs/RUN/
python3 runner/summarize.py RUN
```

`jobs.json` names the tasks, arms, models, attempts and the run, for example
`{"run": "try1", "tasks": [4, 78], "arms": [{"arm": "hermes", "label": "hermes"}], "models": ["deepseek/deepseek-v4.1-flash"], "attempts": 1}`.

## Credits

The tasks come from MemoryArena's group travel planner (dataset under CC BY 4.0). The four interruption types follow
InterruptBench's taxonomy. Neither project's code is included or modified here. Please cite both projects if you use
this benchmark.
