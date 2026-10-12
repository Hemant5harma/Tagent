"""One explicitly authorized demo reset; no broker mutations or trade proposals."""

import asyncio
import json
import shutil
import sys
from pathlib import Path

from sqlalchemy import select, text

from trade_agent.app import TelegramApplicationFacade
from trade_agent.db import ExecutionIntentRecord, ProposalRecord, SystemStateRecord
from trade_agent.draft_extract import explicit_draft
from trade_agent.entry_readiness import entry_blockers
from trade_agent.limits import check_demo_budget
from trade_agent.main import build_runtime
from trade_agent.reporting import now


async def main():
    app, _ = build_runtime()
    service, settings = app.service, app.settings
    assert settings.trading_mode == "demo" and settings.broker_mode == "mt5_http"
    assert (
        settings.max_estimated_loss == 10
        and settings.demo_daily_loss_limit in {50, 300}
        and settings.demo_drawdown_limit in {300, 1000}
    )
    account = service.broker.account(settings.account_id)
    assert (
        account.is_demo and account.currency == "USD" and account.account_id == settings.account_id
    )
    state = service.broker.state(settings.account_id)
    refs = [f"position:{p.position_id}" for p in state.positions] + [
        f"order:{o.order_id}" for o in state.orders
    ]
    assert len(refs) < 5, "No capacity under approved five-exposure limit"
    with service.database.session() as session:
        assert not session.scalars(
            select(ExecutionIntentRecord).where(
                ExecutionIntentRecord.status.in_(["prepared", "unknown", "accepted"])
            )
        ).first(), "Unresolved execution requires review"
    if "--prepare" in sys.argv:
        assert service._paused()
        path, backup = Path(".env"), Path(".env.pre-spec006")
        if not backup.exists():
            shutil.copy2(path, backup)
            backup.chmod(0o600)
        updates = {
            "TRADE_AGENT_MAX_CONCURRENT_EXPOSURES": "5",
            "TRADE_AGENT_MAX_OPEN_POSITIONS": "5",
            "TRADE_AGENT_MAX_PENDING_ORDERS": "5",
            "TRADE_AGENT_DEMO_DAILY_LOSS_LIMIT": "300",
            "TRADE_AGENT_DEMO_DRAWDOWN_LIMIT": "1000",
        }
        lines = [
            line
            for line in path.read_text().splitlines()
            if line.split("=", 1)[0].strip() not in updates
        ]
        path.write_text("\n".join(lines + [f"{k}={v}" for k, v in updates.items()]) + "\n")
        path.chmod(0o600)
        stamp = now()
        with service.database.session() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(830022)"))
            archive_key = f"spec006_authorized_reset:{settings.account_id}"
            assert session.get(SystemStateRecord, archive_key) is None, (
                "Reset already performed; do not reset again"
            )
            key = f"demo_budget:{settings.account_id}"
            budget = session.get(SystemStateRecord, key, with_for_update=True)
            assert budget is not None
            session.add(
                SystemStateRecord(
                    key=archive_key,
                    value=json.dumps(
                        {
                            "previous_budget": json.loads(budget.value),
                            "reviewed_references": refs,
                            "new_equity_baseline": str(account.equity),
                            "authorized_change": "five exposures, $300 daily loss, $1000 evaluation drawdown, new evaluation, resume; preserve current positions",
                        }
                    ),
                    updated_at=stamp,
                )
            )
            budget.value = json.dumps(
                {
                    "started_at": stamp.timestamp(),
                    "high": str(account.equity),
                    "day_high": str(account.equity),
                    "day_start": str(account.equity),
                    "date": stamp.date().isoformat(),
                    "breached": False,
                }
            )
            budget.updated_at = stamp
            session.add(
                SystemStateRecord(
                    key=f"reviewed_external_exposure:{settings.account_id}",
                    value=json.dumps({"references": refs}),
                    updated_at=stamp,
                )
            )
            for row in session.scalars(
                select(ProposalRecord).where(
                    ProposalRecord.account_id == settings.account_id,
                    ProposalRecord.status == "pending_confirmation",
                )
            ):
                row.status, row.updated_at = "cancelled", stamp
        service._event(
            None,
            None,
            "operator_authorized_demo_evaluation_reset",
            {"max_concurrent_exposures": 5, "reviewed_references": refs},
        )
        print(
            "Prepared authorized reset; old budget archived; entries remain paused pending restarted-worker validation"
        )
    elif "--resume" in sys.argv:
        assert (
            settings.max_concurrent_exposures
            == settings.max_open_positions
            == settings.max_pending_orders
            == 5
        )
        assert settings.max_estimated_loss == 10
        assert settings.demo_daily_loss_limit == 300
        assert settings.demo_drawdown_limit == 1000
        assert service._paused()
        with service.database.session() as session:
            assert session.get(SystemStateRecord, f"spec006_authorized_reset:{settings.account_id}")
        facade = TelegramApplicationFacade(app)
        try:
            parsed = explicit_draft("sell XAUUSD lot 0.05 take $10 profit")
            instruction = facade._instruction_from_parse(
                settings.telegram_owner_id, parsed.model_dump()
            )
            validated = service.risk.validate(
                instruction
            )  # read-only MT5 order_check, never order_send
            check_demo_budget(service, validated.account)
            blockers = [
                reason
                for reason in entry_blockers(service)
                if not reason.startswith("New entries are paused.")
            ]
            assert not blockers, blockers
            service.set_paused(False)
            service.reconcile()
            assert not service._paused(), "Reconciliation re-paused; investigate rather than bypass"
            print(
                json.dumps(
                    {
                        "entries_enabled": True,
                        "max_concurrent_exposures": 5,
                        "existing_exposures": len(refs),
                        "broker_preflight": "passed",
                        "estimated_loss": str(validated.estimated_loss),
                        "broker_mutations": 0,
                    }
                )
            )
        except BaseException:
            service.set_paused(True)
            raise
        finally:
            await facade.aclose()
    else:
        print(
            json.dumps(
                {
                    "entries_paused": service._paused(),
                    "max_concurrent_exposures": settings.max_concurrent_exposures,
                    "blockers": entry_blockers(service),
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
