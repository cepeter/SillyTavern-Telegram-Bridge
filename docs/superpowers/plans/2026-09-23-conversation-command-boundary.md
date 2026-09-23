# Conversation Command Boundary Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task-by-task.

**Goal:** Give command-versus-generation dispatch one injected application owner.
**Architecture:** ConversationService composes preparation, command handling,
and generation through required callables; lower modules do not import routers.
**Tech Stack:** Python 3.11+, dataclasses, sqlite3, pytest, AST boundary checks.
**Spec:** docs/superpowers/specs/2026-09-23-conversation-command-boundary-design.md

## Global Constraints
No compatibility aliases or optional service fallbacks. Preserve recovery/durability.
No provider HTTP rewrite, production deployment, or direct main edits.

## Review Focus
Recognized command errors must not become ordinary character generation.
Pending input and committed-operation recovery must still short-circuit.
Queued session, actor and operation identity must reach preparation unchanged.
Generation must use the resolved session model, not the queue default.
Retry and voice must use the injected conversation service without a cycle.

## Task 1: Introduce the service boundary and migrate all consumers
**Files:** create bridge/conversation_service.py and tests/test_conversation_service.py;
modify bridge/message_commands.py, command_routes.py, media.py,
worker_orchestration.py, composition.py, main.py, and canonical-owner tests.
**Interfaces:** ConversationService(prepare_message, dispatch_command, generate_reply);
ConversationService.process_message preserves the prior function signature;
PreparedMessage carries the resolved fields used by the two branches.
- [x] Write tests: `assert "bridge.command_routes" not in imports(message_commands)`;
  assert routing handled/error paths never append a generation event.
- [x] Run `python -m pytest -q tests/test_conversation_service.py`.
  Expected: forbidden-edge and missing-service assertions fail before production edits.
- [x] Move orchestration into the service; make preparation return a typed record.
  `prepared = self.prepare_message(...); if prepared is None: return`
  `if self.dispatch_command(...): return; self.generate_reply(...)`
- [x] Require the service in BridgeServices and build_bridge_services; compose
  canonical functions in main.py and the test factory.
- [x] Replace worker, /retry and voice calls with services.conversation.process_message.
  Remove the old import/function surface and update only affected test owners.
- [x] Run the service, composition, command, retry, voice, and recovery tests.
  Expected: behavior/ownership tests pass with no compatibility shim.
- [x] Run full `python -m pytest -q -n 2 --dist=loadfile`, compileall, and diff --check.
  Expected: zero failures. Record return code explicitly as well as test summary.
- [x] Measure graph against a06a25a; verify no new cyclic modules replace removed ones.
- [x] Complete author self-review and prepare the verified commit.
- [ ] Publish a focused fork PR and verify CI on its exact head; merge remains the user's decision.

## Local verification record
- Initial RED: all 10 boundary/service assertions failed before implementation.
- Voice entry RED: the new injected-service test fails on the unchanged old
  dispatcher, which bypasses the supplied conversation capability.
- Focused service/voice/retired-export guards: 12 passed, exit 0.
- Focused composition/worker/recovery/architecture migration: 135 passed,
  174 subtests passed after canonical-owner corrections.
- Full final-code suite: 751 passed, 476 subtests passed, exit 0.
- compileall and git diff --check: passed.
- Standalone imports: service, command routes, message preparation, media,
  composition, worker orchestration, and main all passed in fresh interpreters.
- Largest SCC / cyclic modules: 30 -> 27; reciprocal pairs: 20 -> 18.
- No newly cyclic module; command_routes, greetings and update leave the SCC.
- Concrete Telegram importers remain 32; transport decoupling is not claimed.
- Review: author self-review; no independent reviewer subagent was available.
- The existing aiohttp async-cleanup RuntimeWarning appeared on one full run;
  the process still exited 0. This PR changes no provider transport code.
