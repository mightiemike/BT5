All cited code has been verified against the production repository. The claim is accurate on every technical point:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` — confirmed at line 231.
2. `SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]` where `sender` is the direct pool caller — confirmed at line 37.
3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making `msg.sender` (= router) the `sender` delivered to the extension — confirmed at lines 72–80.
4. The same substitution applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — confirmed at lines 104, 136, 165.
5. `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the position owner), not `sender` — confirmed at line 38.

The bypass is reachable by any unprivileged user, requires no special privileges, and the precondition (pool admin allowlisting the router) is the only way to enable the standard periphery path for allowlisted users.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on the Direct Pool Caller (`sender`) Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` becomes the router address. A pool admin who allowlists the router to enable the standard periphery path inadvertently grants every unpermissioned user the ability to bypass the per-user swap allowlist, defeating the pool's curation policy and exposing LP assets to unauthorized flow.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So `sender` delivered to the extension is the **router address**, not the end user. The allowlist lookup becomes `allowedSwapper[pool][router]`. A pool admin who wants specific users to trade via the standard periphery must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, so the check passes for **any** caller of the router — including users explicitly not on the allowlist. The same substitution applies to `exactInput` (L103-104), `exactOutputSingle` (L135-137), and `exactOutput` (L165-180). By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the position owner passed explicitly by the pool), which is not substituted by the liquidity adder address, so the deposit path does not share this flaw.

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties or institutional LPs). Once the router is allowlisted to enable the standard periphery path, the allowlist is entirely bypassed by any unpermissioned user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle prices without being on the allowlist, causing direct loss of LP-provided liquidity to unauthorized flow. This meets the "direct loss of user principal or owed LP assets" threshold.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, documented periphery swap path. Any pool admin who enables the allowlist extension and also wants users to use the router will naturally allowlist the router address — it is the only mechanism available to enable router-mediated swaps. The bypass requires no special privileges, no malicious setup, no non-standard tokens, and no off-chain coordination — only a call to a public router function. It is repeatable on every block.

## Recommendation
`SwapAllowlistExtension.beforeSwap` must gate on the **end user** rather than the direct pool caller. The cleanest fix is to have the router forward the original `msg.sender` through `extensionData` and have the extension decode and verify it. However, since `extensionData` is caller-controlled and forgeable, the pool must enforce that only the router (a trusted contract) can supply this field — or the extension must require `sender` to be an EOA (i.e., `sender.code.length == 0`), prohibiting contract intermediaries from being allowlisted. Alternatively, redesign the extension to accept a signed proof of the original initiator forwarded through `extensionData` with router-level authentication.

## Proof of Concept
```
Setup:
  1. Pool admin deploys pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     — intending to let allowlisted users use the standard periphery.
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. Attacker (not on allowlist) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})
  5. Router calls pool.swap(recipient, ...) with msg.sender = router.
  6. Pool calls _beforeSwap(sender=router, ...) → extension.beforeSwap(sender=router, ...).
  7. Extension checks allowedSwapper[pool][router] == true → passes.
  8. Swap executes. Attacker receives output tokens.
  9. Allowlist is bypassed with zero special privileges.

Foundry test outline:
  - Deploy SwapAllowlistExtension, pool, and MetricOmmSimpleRouter.
  - Pool admin calls setAllowedToSwap(pool, address(router), true).
  - Assert attacker (not allowlisted) calling router.exactInputSingle() succeeds.
  - Assert attacker calling pool.swap() directly reverts with NotAllowedToSwap.
```