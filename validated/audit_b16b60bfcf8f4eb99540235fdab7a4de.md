Audit Report

## Title
`SwapAllowlistExtension` gates on the router address instead of the real end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded from the pool, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that value is the router's address, not the actual end user. A pool admin who allowlists the router to enable legitimate users to trade through it simultaneously opens the gate to every unprivileged caller on the network, completely nullifying the access-control intent of the extension.

## Finding Description
The full call chain is confirmed by the production code:

**Step 1 — Router calls pool with itself as `msg.sender`:**
`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly. [1](#0-0) 

**Step 2 — Pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`:**
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, recipient, ...)`, so `sender` is the router address whenever the pool is called via the router. [2](#0-1) 

**Step 3 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:** [3](#0-2) 

**Step 4 — `SwapAllowlistExtension.beforeSwap` gates on that forwarded `sender`:**
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
`msg.sender` here is the pool (correct, prevents spoofing). `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. [4](#0-3) 

**Root cause:** The extension has no mechanism to distinguish the real end user from an intermediary router. Once `allowedSwapper[pool][router] = true`, the guard is satisfied for every caller of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`, regardless of whether that caller is individually allowlisted.

The symmetric failure also exists: if the admin does *not* allowlist the router, individually allowlisted users who route through the router are blocked, making the standard periphery unusable for legitimate participants.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd market makers, whitelisted institutions, or protocol-controlled addresses) loses that restriction entirely for any caller who routes through `MetricOmmSimpleRouter`. The attacker receives fair-value tokens from the pool's reserves at the oracle-derived bid/ask price. LP holders suffer direct loss of principal proportional to the volume the attacker trades. The pool admin's access-control intent is completely nullified. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break bypassed by an unprivileged path.

## Likelihood Explanation
The precondition — the pool admin allowlisting the router — is a natural and expected operational step for any pool that wants its legitimate users to trade through the standard periphery. The bypass requires no privileged access, no special token, and no complex setup: a single call to a public router function is sufficient. Any user who discovers the allowlisted router can exploit it immediately and repeatedly.

## Recommendation
The `SwapAllowlistExtension` must gate on the real end user, not the intermediary. Three sound approaches:

1. **Require the real user identity in `extensionData`** — the router already forwards `extensionData` unchanged to the pool. The extension can require an ABI-encoded user address in that field and verify it against the allowlist. The router would need to inject `msg.sender` into the extension payload before forwarding.

2. **Allowlist at the router level** — add a separate allowlist inside `MetricOmmSimpleRouter` that gates `exactInput*` / `exactOutput*` by `msg.sender` before calling the pool, so the pool-level extension never needs to reason about intermediaries.

3. **Check `recipient` instead of `sender`** — for single-hop swaps the recipient is the actual beneficiary; however this breaks for multi-hop paths where the router is the intermediate recipient.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
    (to let allowlisted users trade via the router)
  alice is NOT individually allowlisted

Attack:
  alice calls router.exactInputSingle({pool: pool, tokenIn: token0, recipient: alice, ...})
  → router calls pool.swap(recipient=alice, ...)  [msg.sender = router]
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  → swap executes; alice receives pool tokens
  → alice repeats until pool liquidity is drained

Corrupted value: allowedSwapper[pool][router] = true is used as authorization
for every caller of the router, not just the router itself. The identity
actually checked diverges from the identity the pool admin intended to gate.
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
