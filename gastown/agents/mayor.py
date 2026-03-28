"""Mayor — the primary coordinator agent.

The Mayor never writes code. It decomposes engineering goals into atomic
work items (beads) and dispatches them as a convoy to PoleCAT workers.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from gastown.agents.base import BaseAgent
from gastown.models import Bead, BeadSpec, Convoy, DecompositionResult, Rig, gen_bead_id
from gastown.storage import GastownDB
from gastown.tools.gt_tools import gt_get_file_tree

MAYOR_SYSTEM = """You are the Mayor of Gastown, a senior engineering coordinator.

Your role:
- Analyze engineering goals and decompose them into discrete, atomic work items called "beads"
- NEVER write code yourself — only coordinate and delegate
- Each bead must be independently completable by one agent working alone
- Beads should be small: change at most 3 files each
- Identify dependencies between beads so work can be ordered correctly
- Think carefully about the repository structure before decomposing

Rules for good beads:
1. One concern per bead (don't mix implementation + tests in one bead unless trivial)
2. Concrete and specific — the worker needs to know exactly what to do
3. List real filenames the worker will likely touch
4. Priority 0 = highest priority (should be done first)
5. If a bead depends on another, name that dependency in depends_on

Output ONLY valid JSON matching the required schema. No preamble, no explanation outside the JSON."""

REVIEW_SYSTEM = """You are the Mayor of Gastown reviewing completed work.
Provide a concise 2-4 sentence summary of what was accomplished, any concerns,
and whether the goal was met. Be direct and factual."""


class Mayor(BaseAgent):
    """Coordinator agent: decomposes goals, dispatches convoys, reviews results."""

    def __init__(self, db: GastownDB, rig: Rig) -> None:
        super().__init__(db, rig)

    async def decompose(self, goal: str, rig: Rig) -> list[Bead]:
        """Decompose a high-level goal into a list of Bead work items.

        Makes a single structured-output LLM call and persists the resulting
        beads to the database.
        """
        file_tree = await gt_get_file_tree(rig.repo_path)

        user_message = {
            "role": "user",
            "content": (
                f"Goal: {goal}\n\n"
                f"Repository: {rig.repo_path}\n\n"
                f"Current files:\n{file_tree}\n\n"
                "Decompose this goal into atomic beads. Output valid JSON only."
            ),
        }

        # Use a tool-call approach for structured output: works across all LiteLLM providers.
        decompose_tool = {
            "type": "function",
            "function": {
                "name": "decompose_goal",
                "description": "Output the structured decomposition of the engineering goal.",
                "parameters": DecompositionResult.model_json_schema(),
            },
        }

        response = await self._call_llm(
            messages=[user_message],
            system=MAYOR_SYSTEM,
            tools=[decompose_tool],
            tool_choice={"type": "function", "function": {"name": "decompose_goal"}},
        )

        tool_calls = self._extract_tool_calls(response)
        if not tool_calls:
            # Fallback: try to parse raw text as JSON
            raw = self._extract_text(response)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(f"Mayor failed to produce structured decomposition. Response: {raw[:500]}")
            decomp = DecompositionResult.model_validate(data)
        else:
            args = tool_calls[0]["arguments"]
            decomp = DecompositionResult.model_validate(args)

        # Persist beads to DB
        beads: list[Bead] = []
        for spec in decomp.beads:
            bead = Bead(
                id=gen_bead_id(),
                rig_id=rig.id,
                title=spec.title,
                description=spec.description,
                priority=spec.priority,
                metadata={
                    "estimated_files": spec.estimated_files,
                    "depends_on": spec.depends_on,
                    "decomposition_summary": decomp.summary,
                },
            )
            await self.db.create_bead(bead)
            beads.append(bead)

        await self.db.log_event(
            "decomposed",
            f"Mayor decomposed goal into {len(beads)} beads: {decomp.summary}",
        )

        return beads

    async def sling(self, beads: list[Bead], rig: Rig) -> Convoy:
        """Create a convoy and mark all beads as in_progress."""
        from gastown.models import BeadStatus, gen_convoy_id

        convoy = Convoy(
            id=gen_convoy_id(),
            rig_id=rig.id,
            bead_ids=[b.id for b in beads],
        )
        await self.db.create_convoy(convoy)

        for bead in beads:
            await self.db.update_bead_status(
                bead.id,
                BeadStatus.IN_PROGRESS,
                convoy_id=convoy.id,
            )
            bead.convoy_id = convoy.id
            bead.status = BeadStatus.IN_PROGRESS

        await self.db.log_event(
            "slang",
            f"Mayor slung convoy {convoy.id} with {len(beads)} beads",
        )
        return convoy

    async def review_results(self, completed_beads: list[Bead]) -> str:
        """Produce a short review summary of completed work."""
        if not completed_beads:
            return "No beads were completed."

        summaries = "\n".join(
            f"- [{b.id}] {b.title}: {b.metadata.get('done_summary', 'completed')}"
            for b in completed_beads
        )

        response = await self._call_llm(
            messages=[
                {
                    "role": "user",
                    "content": f"The following beads were completed:\n{summaries}\n\nProvide your review.",
                }
            ],
            system=REVIEW_SYSTEM,
        )
        return self._extract_text(response).strip()
