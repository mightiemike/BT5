Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating Swapper, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of `pool.swap` — the router address when users route through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (required for allowlisted users to use standard periphery functions), every unprivileged user can bypass the per-user allowlist by calling any of the router's `exact*` functions. There is no configuration that simultaneously permits allowlisted users to use the router while blocking non-allowlisted users.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap` at line 231:

```solidity
_beforeSwap(
  msg.sender,   // <-- this is the router when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` (lines 149–177) forwards `sender` unchanged via `abi.encodeCall` to every configured extension. `SwapAllowlistExtension.beforeSwap` (lines 37–38) then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. When `MetricOmmSimpleRouter.exactInputSingle` (line 72) calls `IMetricOmmPoolActions(params.pool).swap(...)`, the actual originating user's address is stored only in transient callback context via `_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` (line 71) for payment settlement — it is never surfaced to the extension. The same substitution occurs in `exactInput` (line 103), `exactOutputSingle` (line 135), and `exactOutput` (line 162–163). The allowlist check therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` to enforce a KYC-gated or curated swap policy is rendered completely ineffective once the router is allowlisted. Any unprivileged user can execute swaps on the restricted pool at oracle-quoted prices, exposing LP funds to unrestricted market participants and violating compliance constraints the pool was designed to enforce. This is a direct bypass of a configured access-control guard with fund-impacting consequences for LPs on curated pools — matching the "admin-boundary break" and "broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation
The router is the canonical, documented entry point for swaps. Pool admins who want their allowlisted users to access multi-hop routing, exact-output swaps, or deadline-protected single-hop swaps must allowlist the router. This is a predictable operational outcome, not an exotic edge case. Any pool combining `SwapAllowlistExtension` with router support is immediately and completely vulnerable with no intermediate configuration available.

## Recommendation
The extension must gate the economically relevant actor — the originating user — not the intermediate router. The most practical fix is for `MetricOmmSimpleRouter` to append `abi.encode(msg.sender)` to `extensionData` before calling `pool.swap`, and for `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a recognized router address. Alternatively, a trusted router registry can be maintained in the extension, and when `sender` is a known router, the extension decodes the originating caller from `extensionData` for the allowlist check.

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension E
  E.setAllowedToSwap(P, alice, true)       // alice is the only allowed swapper
  E.setAllowedToSwap(P, router, true)      // admin allowlists router so alice can use it

Attack (executed by charlie, who is NOT allowlisted):
  charlie calls router.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient, zeroForOne, amount, limit, "", extensionData)
      [msg.sender inside pool = router address]
      → pool calls E.beforeSwap(router, recipient, ...)
        → check: allowedSwapper[P][router] == true  ✓ (passes)
      → swap executes at oracle price
      → charlie receives token output

Result:
  charlie, a non-allowlisted user, successfully swaps on a pool
  restricted to alice only. LP funds are exposed to any market participant.
```

Confirmed by:
- `MetricOmmPool.sol` line 231: `_beforeSwap(msg.sender, ...)` passes the router as `sender`
- `ExtensionCalling.sol` lines 160–176: `sender` forwarded unchanged to extension
- `SwapAllowlistExtension.sol` line 37: checks `allowedSwapper[msg.sender][sender]` (pool→router, not pool→user)
- `MetricOmmSimpleRouter.sol` line 71: user address stored only in transient callback context, never in `extensionData`