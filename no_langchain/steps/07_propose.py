# -*- coding: utf-8 -*-
"""Propose the next MODIFY config from econ_result.json. No LangChain."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
import propose  # noqa: E402


def main() -> int:
    import debate

    cli = common.parser("Propose the next ligand-free config, then debate it.")
    cli.add_argument("--model", default=None, help="Proposer model. Same as --propose-model.")
    cli.add_argument("--propose-model", default=None, help="Chat model that writes and defends the next config.")
    cli.add_argument("--adversary-model", default=None, help="Chat model that attacks the next config.")
    cli.add_argument("--verdict-model", default=None, help="Chat model that returns keep or reject.")
    cli.add_argument("--ask", action="store_true", help="Ask which proposer model and which verdict model to use.")
    args = cli.parse_args()
    work = common.work_dir(args)
    root = common.loop_case(work)
    logs = common.agent_dir(work)
    propose_name = args.propose_model or args.model
    models = debate.resolve_models(propose_name, args.verdict_model, args.adversary_model, args.ask)
    propose.propose_once(root, models=models, agent_dir=logs)
    print(f"WORK={root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
