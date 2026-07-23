### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the curated-pool swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `pool.swap()`. When a user enters through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit router-based swaps), the allowlist is completely bypassed for every user on Earth.

---

### Finding Description

**Hook plumbing — wrong-actor binding on the swap allowlist path.**

`MetricOmmPool.swap` captures `msg.sender` and forwards it as `sender` to every before-swap extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` re-encodes that value verbatim:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = msg.sender of pool.swap()
)
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
function beforeSwap(address sender, ...)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

At this point `msg.sender` inside `pool.swap` is the **router address**, so `sender` delivered to the extension is the router, not the end user. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**The dilemma is inescapable:**

| Router allowlist state | Effect |
|---|---|
| Router **is** allowlisted | Every user bypasses the curated-pool gate by routing through the router |
| Router **is not** allowlisted | Legitimate allowlisted users cannot use the router at all |

There is no configuration that simultaneously allows router-based swaps and enforces per-user access control.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) loses that guarantee entirely. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and trade on the restricted pool as long as the router is allowlisted. This constitutes a complete access-control bypass on the curated-pool swap path, with direct fund-flow consequences: non-allowlisted users can drain liquidity, move the oracle-anchored price, or extract fees from LP positions that were intended to be protected.

---

### Likelihood Explanation

The router is the canonical public entry point for swaps. Any pool admin who wants users to be able to swap through the router must allowlist it. The bypass is therefore triggered by the normal, expected deployment configuration. No special privilege, flash loan, or exotic token is required — a single `exactInputSingle` call from any EOA suffices.

---

### Recommendation

The extension must gate on the **economic actor** (the end user), not the intermediary. Two sound approaches:

1. **Pass the original caller through the router.** Add a `recipient`-style `originator` field to the swap call or use a transient-storage slot (already used by the router for payer tracking) so the pool can forward the true initiator to extensions.

2. **Check `sender` only when the caller is not a known periphery contract; otherwise read the transient payer slot.** The router already stores the real payer in transient storage (`_getPayer()`); the extension could read that slot when `sender` is a recognized router.

The simplest correct fix is option 1: have `MetricOmmSimpleRouter` store `msg.sender` in a transient slot before calling the pool, and have the pool (or the extension) read it as the authoritative identity for allowlist checks.

---

### Proof of Concept

```
Setup
─────
1. Deploy MetricOmmPool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so router-based swaps are not blocked.
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack
──────
4. attacker (non-allowlisted EOA) calls:
       router.exactInputSingle({
           pool:      <curated pool>,
           recipient: attacker,
           ...
       })

5. Router calls pool.swap(attacker, ...) with msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
   Hook passes.

8. Swap executes. attacker receives output tokens.
   The curated-pool allowlist was never consulted for attacker's address.
```

The `sender` the extension sees is the router address (step 6), not `attacker`. Because the router is allowlisted (step 2), the gate opens for every caller regardless of their individual allowlist status. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
