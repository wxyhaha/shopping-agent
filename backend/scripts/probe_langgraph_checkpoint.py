import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict


class ProbeState(TypedDict):
    steps: list[str]
    decision: str


def build_graph(checkpointer: SqliteSaver):
    def prepare(state: ProbeState) -> dict[str, Any]:
        return {"steps": [*state["steps"], "prepared"]}

    def approval(state: ProbeState) -> dict[str, Any]:
        answer = interrupt({"kind": "review", "step": state["steps"][-1]})
        return {
            "steps": [*state["steps"], "resumed"],
            "decision": str(answer["decision"]),
        }

    builder = StateGraph(ProbeState)
    builder.add_node("prepare", prepare)
    builder.add_node("approval", approval)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "approval")
    builder.add_edge("approval", END)
    return builder.compile(checkpointer=checkpointer)


def main() -> None:
    with TemporaryDirectory(prefix="shopping-agent-langgraph-") as temp_dir:
        database_path = Path(temp_dir) / "checkpoints.sqlite3"
        config = {"configurable": {"thread_id": "t020-resume-check"}}

        with SqliteSaver.from_conn_string(str(database_path)) as checkpointer:
            graph = build_graph(checkpointer)
            graph.invoke({"steps": [], "decision": ""}, config)
            paused = graph.get_state(config)
            has_interrupt = any(task.interrupts for task in paused.tasks)
            if not has_interrupt:
                raise RuntimeError("图未在人工确认节点暂停。")

        with SqliteSaver.from_conn_string(str(database_path)) as reopened_checkpointer:
            resumed_graph = build_graph(reopened_checkpointer)
            result = resumed_graph.invoke(
                Command(resume={"decision": "approved"}),
                config,
            )

        if result["steps"] != ["prepared", "resumed"] or result["decision"] != "approved":
            raise RuntimeError("检查点重开后的恢复状态与预期不符。")

        print(
            json.dumps(
                {
                    "checkpoint_database_reopened": True,
                    "paused_for_review": has_interrupt,
                    "resumed_decision": result["decision"],
                    "steps": result["steps"],
                    "result": "passed",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
