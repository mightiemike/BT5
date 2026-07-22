### Title
`SwapAllowlistExtension` checks the router's address instead of the actual trader, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` of the `swap` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual end user. The extension therefore checks the router's address, not the trader's address. If the router is allowlisted (the only way to let allowlisted users use the router), every user on the network can bypass the allowlist by routing through the router.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — `_beforeSwap` forwards that value unchanged to every configured extension.** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `sender` (the pool's `msg.sender`) against the allowlist.**

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap` — which is the **router**, not the end user, when the user goes through the periphery. [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making itself `msg.sender` to the pool.**

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

The router does not forward the original `msg.sender` (the end user) to the pool in any way that the extension can observe. The pool's `msg.sender` is always the router contract address.

**The dilemma this creates for pool admins:**

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the network can bypass the allowlist |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers) can be bypassed by any unprivileged user simply by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The non-allowlisted user executes a real swap, receives output tokens, and the pool's curation policy is silently violated. This constitutes a direct loss of the pool's access-control invariant and enables unauthorized trading against LP funds in a pool that was explicitly designed to be restricted.

---

### Likelihood Explanation

The router is the primary user-facing entrypoint documented and deployed for the protocol. Any user who discovers the allowlist restriction on a pool can trivially route through the router instead of calling the pool directly. No special privileges, flash loans, or multi-transaction setup are required. The trigger is a single public call to `exactInputSingle`.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **economic actor** (the end user), not the intermediary contract. Two approaches:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` pass the original `msg.sender` as a dedicated `sender` parameter to `pool.swap`, and have the pool forward that value (rather than its own `msg.sender`) to extensions. This requires an interface change.

2. **Check `tx.origin` as a fallback** (weaker, not recommended for general use): The extension could check `tx.origin` when `sender` is a known router. This is fragile and not recommended.

3. **Preferred — check `sender` at the pool level before extension dispatch**: The pool could expose a `swapFrom(address realSender, ...)` entry point callable only by allowlisted routers, and pass `realSender` to extensions. This preserves composability while keeping the identity check correct.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only `alice` is allowlisted
pool.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router so alice can use it:
pool.setAllowedToSwap(address(pool), address(router), true);

// Attack: bob (not allowlisted) calls the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// bob's swap succeeds — the extension saw sender=router (allowlisted), not bob
```

The extension checks `allowedSwapper[pool][router] == true` and passes. Bob receives output tokens from a pool he was never authorized to trade on. [5](#0-4) [1](#0-0) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L91-99)
```text
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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
