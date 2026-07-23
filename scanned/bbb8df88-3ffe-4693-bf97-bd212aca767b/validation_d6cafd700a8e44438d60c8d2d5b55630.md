### Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the `sender` the extension sees is the **router's address**, not the actual end-user. If the pool admin allowlists the router (the natural configuration for a pool that accepts router-mediated swaps), every user — including those explicitly not on the allowlist — can bypass the gate by calling through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the `sender` parameter and dispatches it to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...)
)
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the allowlist:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
``` [4](#0-3) 

So `msg.sender` received by the pool is the **router contract address**. The actual end-user (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback — it is never forwarded to the extension. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This is the direct analog of the external bug: just as the `orderType` omits `isLong` and `reduceOnly`, making two structurally different orders hash identically, the `beforeSwap` allowlist check omits the actual end-user identity, making every user who routes through the router appear identical (as the router address) to the gate.

---

### Impact Explanation

**Direct loss of access-control integrity with fund-impacting consequences.**

A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swapping to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). To allow those addresses to use the standard router, the admin adds the router to the allowlist (`setAllowedToSwap(pool, router, true)`). From that point on, **any address** — including those the admin explicitly never allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle` and swap against the pool without restriction. The allowlist is completely neutralized for all router-mediated swaps.

Consequences:
- Unauthorized users drain pool liquidity at oracle-anchored prices, extracting value from LPs who deposited under the assumption that only vetted counterparties could trade.
- Protocol-fee and admin-fee revenue accrues from trades the pool admin never intended to permit.
- The pool's risk model (e.g., limiting exposure to specific counterparties) is silently broken.

---

### Likelihood Explanation

**Medium.** The scenario requires the pool admin to allowlist the router — a natural and expected operational step for any pool that wants to support the standard periphery. The admin has no way to simultaneously allow router-mediated swaps for approved users and block router-mediated swaps for unapproved users, because the extension receives no information about the real end-user. Any pool that enables the router for allowlisted users is automatically vulnerable.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end-user — not the intermediary. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should forward the original `msg.sender` as part of `extensionData` so extensions can decode and verify it. This requires a convention between the router and extensions.

2. **Extension-side (preferred)**: Change `SwapAllowlistExtension.beforeSwap` to check `recipient` (the address receiving output tokens) or require the router to encode the real user in `extensionData` and decode it in the extension. Alternatively, gate on `sender` only when `sender` is an EOA, and require a signed proof of identity when `sender` is a contract.

The simplest safe fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and check that value when `sender` is a known router address.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls:
       swapExtension.setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for approved users.
3. Attacker (address NOT in allowedSwapper[pool]) calls:
       router.exactInputSingle({pool: pool, recipient: attacker, ...})
4. Router calls pool.swap(attacker, ...) — msg.sender to pool = router.
5. Pool calls extension.beforeSwap(router, attacker, ...).
6. Extension checks allowedSwapper[pool][router] == true → passes.
7. Attacker receives output tokens. Allowlist is bypassed.
```

The attacker never interacts with the pool directly and never appears in the allowlist, yet completes a swap because the extension's identity check covers only one dimension of the caller identity (the intermediary contract) while omitting the actual end-user — the exact same incomplete-field pattern as the external `orderType` bug.

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
```

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
