### Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the router contract, not the end user. A pool admin who allowlists the router address to enable router-mediated swaps for their permitted users inadvertently opens the allowlist to every user on the network.

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and every other router entry point) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The pool admin has two mutually exclusive options:

| Admin choice | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Do **not** allowlist the router | **Blocked** (broken UX) | Blocked |
| **Allowlist the router** | Allowed | **Also allowed — bypass** |

There is no configuration that simultaneously allows allowlisted users to use the router while blocking non-allowlisted users. Allowlisting the router — the natural action for a pool that is meant to be accessible through the standard periphery — collapses the per-user gate to a per-contract gate, letting any address bypass the restriction.

### Impact Explanation

A pool admin deploys a restricted pool (e.g., for institutional market makers or KYC'd counterparties) and configures `SwapAllowlistExtension`. To let their approved users trade through the standard router, they call `setAllowedToSwap(pool, router, true)`. From that point, any address on the network can call `router.exactInputSingle(...)` and the `beforeSwap` hook passes because `sender == router` is allowlisted. Non-approved users can drain arbitrage value from the pool's LP positions, which were priced assuming only trusted counterparties would trade. This is a direct loss of LP principal through unauthorized swap execution in a pool whose access control is supposed to prevent it.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point documented and deployed by the protocol. Any pool admin who wants their allowlisted users to have a normal trading UX will allowlist the router. The bypass requires no special privilege — any EOA or contract can call the router. The trigger is a routine admin configuration step, not an exotic attack setup.

### Recommendation

Pass the economically relevant actor — the end user — through the hook chain rather than the direct caller. One approach: have the router store the originating user in transient storage (it already stores `payer` for the callback) and expose it via a standard interface that the pool reads and forwards as `sender` to extensions. Alternatively, `SwapAllowlistExtension` can check `recipient` when `sender` is a known router, but this is fragile. The cleanest fix is for the pool to accept an explicit `swapper` parameter (separate from `msg.sender`) that the router populates with `msg.sender` before calling the pool, and for the extension to gate on that value.

### Proof of Concept

```solidity
// Pool admin sets up a restricted pool and allowlists the router
swapAllowlist.setAllowedToSwap(pool, address(router), true);
// alice is NOT individually allowlisted
// alice calls the router — beforeSwap sees sender=router, which IS allowlisted → passes
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    recipient: alice,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// alice successfully swaps in a pool she was never allowlisted for
```

The `beforeSwap` check at line 37 of `SwapAllowlistExtension` evaluates `allowedSwapper[pool][router]` — true — and returns the success selector, bypassing the per-user gate entirely. [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```
