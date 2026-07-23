Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` of `pool.swap()` is the router contract, not the end user. Any pool admin who allowlists the router (required for router-mediated swaps to work) simultaneously opens the pool to all users, rendering the allowlist ineffective.

## Finding Description
**Root cause — pool passes `msg.sender` as `sender`:**
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)` at lines 230–240. When the router calls `pool.swap()`, `msg.sender` is the router contract address.

**`ExtensionCalling._beforeSwap` forwards this unchanged:**
Lines 149–177 of `ExtensionCalling.sol` encode `sender` (the router address) and pass it to every registered extension via `_callExtensionsInOrder`.

**`SwapAllowlistExtension.beforeSwap` checks the router, not the end user:**
Line 37 of `SwapAllowlistExtension.sol`:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```
Here `msg.sender` is the pool (correct) and `sender` is the router address (not the end user). The check becomes `allowedSwapper[pool][router]`.

**`MetricOmmSimpleRouter.exactInputSingle` forwards no user identity:**
Lines 72–80 of `MetricOmmSimpleRouter.sol` call `pool.swap(params.recipient, ...)`. The actual caller (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the pool or extensions.

**The dilemma:**
- If admin does **not** allowlist the router → allowlisted users cannot use the standard router UX.
- If admin **does** allowlist the router → `allowedSwapper[pool][router] = true`, and every user on the network bypasses the allowlist by routing through `MetricOmmSimpleRouter`.

## Impact Explanation
The swap allowlist access control is completely ineffective for router-mediated swaps. Any unprivileged user can bypass the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting a restricted pool. This allows unauthorized parties to trade against LP funds in pools intended to be restricted (KYC-gated, counterparty-restricted, compliance-controlled). This constitutes a broken core pool functionality and an admin-boundary break where an unprivileged path bypasses pool admin access control, directly exposing LP funds to unauthorized swaps.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the standard user-facing swap entrypoint. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router, which simultaneously opens the gate to all users. The bypass requires no special privileges, no flash loans, and no complex setup — a single `exactInputSingle` call suffices. The condition is trivially reachable by any network participant.

## Recommendation
Pass the actual end user's address through the extension call chain rather than the immediate `msg.sender`. One approach: add a `swapper` parameter to `pool.swap()` that callers supply explicitly (validated against `msg.sender` or a trusted router registry), and pass that as `sender` to extensions. Alternatively, `SwapAllowlistExtension` should require the router to forward the originating user via `extensionData`, with the extension decoding and verifying it (e.g., checking an EIP-712 signature binding the user to the swap parameters).

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)  — only alice should swap.
3. Admin calls setAllowedToSwap(pool, router, true) — required for alice to use the router.
4. Bob (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool: restrictedPool,
           recipient: bob,
           zeroForOne: true,
           amountIn: X,
           ...
       }))
5. pool.swap() is called with msg.sender = router.
6. _beforeSwap(router, ...) → SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Bob's swap executes successfully despite not being on the allowlist.
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only `alice` and the router, call `exactInputSingle` from `bob`, assert the call succeeds and `bob` receives output tokens.