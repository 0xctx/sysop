# Race mode (`--race N`)

Use only when the request says `--race N` (or "race it"). Several vendors build the same ticket in parallel; the first green, READY result lands. The flag is the operator's authority for cleaning up the losers, and their work is archived, never deleted.

1. **Start**, from the main checkout, with the ticket's brief as usual:
   `S race start <id> --n <N> --size <size> --kind <kind> --prompt-file <store>/briefs/<id>.md`.
   It routes N different vendors (refusing with fewer than two) and launches each racer in its own worktree, as ticket `<id>-r1` … `<id>-rN`, on branch `race/<id>/rK`.
2. **Wait** with `S wait <id>-r1 … <id>-rN` (bounded; repeat while exit 124). A racer that fails for availability is simply out; there is no fallback inside a race.
3. **Check finishers in order of finishing**: run the `sysop:check` skill in each finished racer's worktree. A red racer is out.
4. **Review the first green racer** with the `sysop:review` skill (its branch, and its vendor as the author's family). If the verdict is READY, it wins. If not, review the next green racer. If no green racer reaches READY, take the best one into the normal correction pass (fleet step 7) and treat it as the winner.
5. **Settle**: `S race settle <id> --winner rK`. Every other racer is stopped, its work (including uncommitted changes) is archived under `refs/sysop/race/<id>/rJ`, and its worktree and branch are removed. It writes each loser's ledger row itself: add only the winner's.
6. **Land the winner** as any fleet ticket: `S land race/<id>/rK --base <base>` under authority, then its ledger row with `--ticket <id>-rK` (where its launch records are).
7. Report the race in the fleet summary: each racer's vendor, its finish order, check result, review verdict, and the archive refs (`git log refs/sysop/race/<id>/rJ` shows a loser's work).
