from __future__ import annotations

import argparse
import sys

from .compaction import CompactionConfig, Compactor
from .config import load_config
from .hooks import HookRegistry, register_default_hooks
from .loop import AgentLoop
from .model_client import ChatCompletionsClient
from .tools import build_builtin_registry


def build_agent(*, verbose: bool = True) -> AgentLoop:
    config = load_config()
    registry = build_builtin_registry(config.cwd)
    hooks = HookRegistry()
    register_default_hooks(hooks, verbose=verbose)
    compactor = Compactor(
        CompactionConfig(
            state_dir=config.state_dir,
            auto_compact_ratio=config.auto_compact_ratio,
        )
    )
    client = ChatCompletionsClient(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
    )
    return AgentLoop(
        config=config,
        model_client=client,
        tool_registry=registry,
        hooks=hooks,
        compactor=compactor,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onecode", description="Minimal CLI code agent.")
    parser.add_argument("prompt", nargs="*", help="Prompt to run once. Omit for interactive mode.")
    parser.add_argument("--quiet", action="store_true", help="Hide tool and compact logs.")
    args = parser.parse_args(argv)

    agent = build_agent(verbose=not args.quiet)
    if args.prompt:
        result = agent.run(" ".join(args.prompt))
        if result:
            print(result)
        return 0

    print("onecode interactive. Use /exit, /clear, /compact, /tools.")
    while True:
        try:
            prompt = input("onecode> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt:
            continue
        if prompt in {"/exit", "exit", "quit"}:
            return 0
        if prompt == "/clear":
            agent.state.messages.clear()
            print("[cleared]")
            continue
        if prompt == "/tools":
            for tool in agent.tool_registry.tools():
                print(
                    f"{tool.meta.name}: read_only={tool.meta.read_only}, "
                    f"concurrency_safe={tool.meta.concurrency_safe}"
                )
            continue
        if prompt == "/compact":
            agent.compactor.full_compact(
                agent.state,
                model_client=agent.model_client,
                hooks=agent.hooks,
                reason="manual compact",
            )
            print("[compacted]")
            continue
        result = agent.run(prompt)
        if result:
            print(result)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
