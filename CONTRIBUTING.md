# 🤝 Contributing to sysop

Thank you for helping! sysop stays **small on purpose**: a change is welcome when it makes delivery more trustworthy, cheaper, or easier, without adding a server, a database, a build step or a third-party Python dependency.

## 🗺️ Where things live
- `skills/`: judgement (what to do, in plain markdown);
- `scripts/`: mechanics (`sysop.py` and standard-library modules, unit-tested);
- `hooks/`: guarantees (the guard, session context, the Stop gate) and the mod (`sysop.mod.jsx`);
- `presets/`: model setups as JSON.

Read [`AGENTS.md`](AGENTS.md) (the rules every change follows) and [`docs/architecture.md`](docs/architecture.md) first.

## ✅ Before you open a PR

```
python3 -m unittest discover -s tests     # offline: agent CLIs are faked
python3 scripts/lint.py .                 # skill craft and token budgets
claude plugin validate .                  # needs Claude Code
claude plugin test .                      # the mod's tests; needs Claude Code
```

End-to-end runs (`tests/e2e/`, `python3 scripts/sysop.py e2e <name>`) drive real headless Claude Code sessions and **spend real tokens**: run the one your change touches, and say which in the PR.

## 📏 House rules
- Mechanics go in `scripts/` with a test; judgement goes in a skill. A skill never does by hand what a `sysop.py` command can do.
- New capabilities are opt-in; defaults never need a paid key.
- Plain words in anything a person reads. Every refusal says why and the one fix.
- No AI attribution in commits or PRs.

## 🧪 Adding a preset
Copy a file in `presets/`, keep `needs`, `optional`, `capacity` and `caveats` honest, and run the tests: every shipped preset must pass the safety rules (`tests/test_presets.py`).

Questions? Open an issue. 💬
