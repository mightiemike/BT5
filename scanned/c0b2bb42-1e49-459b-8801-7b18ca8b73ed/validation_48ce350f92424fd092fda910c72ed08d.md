### Title
`SwapAllowlistExtension` checks router address instead of actual swapper for router-mediated swaps, enabling full allowlist bypass when the router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap`. When `MetricOmmSimpleRouter` calls `pool.swap`, `sender` equals the router's address, not the originating user. If a pool admin allowlists the router to support router-mediated swaps for their approved users, every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

`msg.sender` inside the extension is the pool (the pool calls the extension). `sender` is whatever the pool passed as the first argument to `_beforeSwap`. The pool always passes its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards it unchanged:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, the router is `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

A pool admin who wants allowlisted users to be able to trade through the router has only two options:

| Admin choice | Effect |
|---|---|
| Allowlist individual users only | Allowlisted users **cannot** use the router (router not allowlisted → reverts) |
| Allowlist the router address | **Every** user can swap through the router — per-user gate is fully bypassed |

There is no configuration that allows specific users to trade through the router while blocking others. The moment the router is allowlisted, the allowlist is void for all router-mediated swaps.

---

### Impact Explanation

Any user who is not on the allowlist can call `MetricOmmSimpleRouter.exactInputSingle` (or any multi-hop variant) against a pool whose admin has allowlisted the router. The extension passes because `allowedSwapper[pool][router] == true`. The unauthorized user executes a swap at oracle-derived prices, extracting value from LP bins. Because Metric OMM pools price from an external oracle, a non-allowlisted actor can time swaps against stale or favorable oracle quotes, draining LP principal. This is a direct loss of LP assets and a broken core pool invariant (the allowlist).

---

### Likelihood Explanation

A pool admin who deploys a restricted pool and also wants their approved users to access the router will naturally allowlist the router address — this is the only way to make router swaps work at all. The mistake is non-obvious: the admin believes they are enabling router access for their approved set, but the extension has no way to distinguish users behind the router. The trigger requires no privileged escalation beyond the semi-trusted pool admin's own configuration step; any unprivileged address can then exploit it.

---

### Recommendation

The `SwapAllowlistExtension` must verify the originating user, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` and have the extension decode and verify it. The pool already forwards `extensionData` unchanged to every hook.
2. **Separate router-aware allowlist**: Add a second mapping `allowedSwapperViaRouter[pool][user]` and have the router encode the user address in `extensionData`; the extension checks the direct mapping for direct calls and the router mapping for router calls, verifying the encoded address matches a trusted router.

Either approach must be paired with a check that the encoded address is not forgeable (i.e., the extension must verify `msg.sender` is a trusted router before trusting the encoded user address).

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` in the `beforeSwap` order.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is approved.
3. Admin calls `setAllowedToSwap(pool, router, true)` — intending to let `userA` trade via the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` — `msg.sender` of `pool.swap` = router.
6. Pool calls `extension.beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` = `true` → passes.
7. `userB` completes the swap, extracting tokens from LP bins at oracle price, with no allowlist enforcement.

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
