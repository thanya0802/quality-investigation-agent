"""python -m qia.cli ask "question" [--inv INV_ID]   |   python -m qia.cli audit INV_ID"""
import argparse
from . import audit
from .agent.graph import ask

def main():
    p = argparse.ArgumentParser(prog="qia")
    sp = p.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("ask"); a.add_argument("question"); a.add_argument("--inv", default=None)
    t = sp.add_parser("audit"); t.add_argument("inv_id")
    args = p.parse_args()
    if args.cmd == "ask":
        st = ask(args.question, inv_id=args.inv)
        print(f"investigation: {st['inv_id']}  intent: {st['intent']}  engine: {st['engine']}  "
              f"answer_by: {st['answer_by']}  validation: {st['validation']}\n")
        print(st["answer"])
        print(f"\nContinue this investigation with: --inv {st['inv_id']}")
    else:
        for r in audit.trail(args.inv_id):
            print(f"{r['created_at']}  {r['action']:<5} {r['name']:<20} {r['status']:<6} rows={r['row_count']:<4} "
                  f"{r['latency_ms']}ms  {r['input'][:80]}")

if __name__ == "__main__":
    main()
