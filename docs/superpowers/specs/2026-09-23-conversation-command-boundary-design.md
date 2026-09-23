# ConversationService / command-dispatch boundary

Date: 2026-09-23
Base: a06a25a14887702265c37374c7b3bf1ae1dc2652
Parent: Runtime Architecture Migration master design; graph-guided retirement.

## Intent
Retire `message_commands -> command_routes` without merely relocating the cycle.
Keep commands, pending inputs, operation recovery, group selection, session/model
resolution, retry, voice transcription, and generated reply delivery behavior.
This is the highest-priority architectural cut from the merged 30-module SCC.

## Selected design
A new `ConversationService` owns prepare -> command dispatch -> generation.
It receives three required callable collaborators from the composition root:
`prepare_message`, `dispatch_command`, and `generate_reply`.
`PreparedMessage` carries the resolved command, session, model, persona, fields,
group turn/context, and request context between preparation and dispatch.

`message_commands.prepare_message()` retains the current early-handled workflows
(reset, pending input, session menu, committed operation recovery). It returns None
when those workflows handle a message; otherwise it returns PreparedMessage.
It never imports/calls command routing. Reply generation stays at its existing
canonical owner for this bounded migration.

`ConversationService.process_message()` preserves the current external signature.
It returns after preparation handles a message or command routing returns True.
Only an unhandled message reaches generation. A routing exception propagates;
it must never fall through into provider generation.

`BridgeServices.conversation` is required, constructed once at startup with
canonical collaborators. Worker, retry, and voice paths call that injected service.
No production alias remains for `message_commands.process_message`.
The service and its data record import no bridge adapters, routers, composition
module, globals, or service locator. Dynamic imports do not replace retired edges.

## Alternatives
Moving the old function into a module imported by routes/media just moves the cycle.
Returning command actions would redesign every route contract unnecessarily.
An injected three-collaborator application service removes the back edges while
preserving existing handler contracts and explicit per-request services.

## Parallelism and limits
Production files: conversation_service.py, message_commands.py, command_routes.py,
media.py, worker_orchestration.py, composition.py, main.py.
The callback-token purity branch touches none of them. Run validation concurrently
in separate worktrees. Re-scan merged main before choosing another cycle cut.
GroupService, provider transport extraction, route_update decomposition, broad
transaction cleanup, lint/type rollout, and branch protection are later milestones.
The prior analysis's streaming-continuation defect is not silently fixed here.

## Acceptance and review
Prove forbidden imports with AST tests; prove service isolation in a fresh Python
process. Test dispatch short-circuit, plain generation, pending-input short-circuit,
command-error propagation, and unchanged argument/session/actor/model forwarding.
Keep operation-recovery, retry, voice, command and full regression suites green.
Measure largest SCC and all cyclic modules before/after; reject replacement cycles.
No broad formatting or compatibility re-export. Publish a focused PR, not main edits.

## Subsequent checkpoints
1. Review and merge the conversation boundary and independent token read-purity
   changes. Refresh the second branch after the first merge and rerun tests.
2. Re-scan the actual merged graph; choose the next cut from that evidence,
   rather than freezing a list of cycle PRs from this baseline.
3. Continue transaction ownership in independently testable slices; complete
   GroupService and then the ModelRouter/provider port boundary.
4. Move application Telegram effects toward injected runtime/ports.
5. Decompose route_update only after higher-value cycle/service ownership work.
6. Finish terminology cleanup, incremental Ruff and service/port static typing,
   dependency-direction CI checks, and branch protection requiring CI.

The previous analysis's confirmed streaming-continuation finding remains open:
`generation.py` recursively continues a visible length-stopped stream without
checking a continuation bound at that branch, and does not forward streaming or
cancellation callbacks there. This is separate provider/generation work; it is
not silently treated as fixed by the command-dispatch extraction.
