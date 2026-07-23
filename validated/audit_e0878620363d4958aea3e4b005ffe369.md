### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router's address. If the pool admin allowlists the router (required for any legitimate user to use it), every non-allowlisted user can bypass the curated-pool restriction by routing through the public router.

### Finding Description

**Step 1 – Pool passes `msg.sender` as `sender` to the hook.**

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument forwarded to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
``` [2](#0-1) 

**Step 2 – SwapAllowlistExtension gates on that `sender` value.**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct for the pool-keyed mapping), and `sender` is whoever called `pool.swap()`.

**Step 3 – MetricOmmSimpleRouter calls `pool.swap()` as itself.**

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

When this executes, `msg.sender` inside `pool.swap()` is the **router address**, not the end user. The hook therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**The dilemma this creates for pool admins:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every non-allowlisted user can bypass the restriction by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

### Impact Explanation

Any non-allowlisted user can trade on a curated pool that deploys `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`). The pool receives and settles the swap normally; the only guard that was supposed to block the trade silently passes because it sees the router's address, which the admin had to allowlist. This is a direct, fund-impacting policy bypass: the pool's LP positions are exposed to counterparties the pool admin explicitly intended to exclude.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless contract. Any user who knows the pool uses a swap allowlist can trivially route through the router to bypass it. No special privileges, flash loans, or multi-step setup are required. The bypass is a single transaction.

### Recommendation

The extension must gate the **economic actor**, not the intermediary. Two viable approaches:

1. **Check `tx.origin` inside the extension** (acceptable only if the threat model excludes contract-based attackers; generally fragile).
2. **Decode the real swapper from `extensionData`**: require the router to embed the original `msg.sender` in the extension payload, and have the extension verify a signed or trusted identity from that payload.
3. **Preferred – check `recipient` instead of `sender`**: for swap allowlists the recipient is the party that receives value and is harder to spoof than the intermediary. Alternatively, document that the allowlist gates the direct pool caller and that the router must never be allowlisted on curated pools, and enforce this with a factory-level check that rejects pools pairing `SwapAllowlistExtension` with a known router address in the allowlist.

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin must do this for legitimate users
  allowedSwapper[pool][alice]  = true   // alice is the intended allowlisted user
  allowedSwapper[pool][bob]    = false  // bob is explicitly excluded

Attack (bob bypasses the allowlist):
  bob calls router.exactInputSingle({
      pool: pool,
      zeroForOne: true,
      amountIn: X,
      recipient: bob,
      ...
  })

  router calls pool.swap(bob, true, X, ...)
    → msg.sender inside pool.swap() = router
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓ passes
    → swap executes, bob receives tokens
```

Bob successfully trades on a pool that was supposed to exclude him. The invariant "only allowlisted addresses may swap on this pool" is broken for every pool that must also support router-mediated swaps.

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
