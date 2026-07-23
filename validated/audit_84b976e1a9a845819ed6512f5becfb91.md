Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender`, which the pool sets to `msg.sender` of the `pool.swap` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the gate to every address that calls the router, completely defeating the per-address allowlist.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, `_beforeSwap` is invoked with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value directly to the extension via `abi.encodeCall`: [2](#0-1) 

**Step 2 — When the call originates from `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router.**

`exactInputSingle` (and all `exact*` variants) calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [3](#0-2) 

**Step 3 — Extension checks the router address, not the end user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (= router) and evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]`: [4](#0-3) 

The actual end user's address is never examined.

**Step 4 — The only viable admin configuration opens the gate to everyone.**

A pool admin who wants allowlisted users to swap through the router must call `setAllowedToSwap(pool, router, true)`: [5](#0-4) 

Once set, the guard passes for **any** caller that routes through the router. There is no configuration that simultaneously (a) allows allowlisted users to use the router and (b) blocks non-allowlisted users from using the same router.

**Contrast with `DepositAllowlistExtension`.**

The deposit-side extension correctly checks `owner` (the actual LP owner, explicitly forwarded as a separate parameter), not `sender` (the adder contract): [6](#0-5) 

The swap-side extension has no equivalent forwarded identity, so it is structurally unable to distinguish individual users when the router is the direct caller.

## Impact Explanation

Any non-allowlisted address can trade on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, or `exactOutput`, provided the pool admin has allowlisted the router. The allowlist — intended to enforce KYC, whitelist-only access, or regulatory restrictions — is completely bypassed. Trades execute at oracle prices, so the bypass directly results in unauthorized token flows through the pool. This constitutes a broken core access-control mechanism causing unauthorized fund flows, qualifying as High severity under Sherlock thresholds.

## Likelihood Explanation

A pool admin who deploys a curated pool and wants their allowlisted users to access the standard router will naturally call `setAllowedToSwap(pool, router, true)`. This is the expected operational step; nothing in the interface or documentation signals that doing so opens the gate to all users. The prerequisite is therefore a routine, well-motivated admin action rather than a misconfiguration. Any unprivileged address can then exploit it by simply calling the router.

## Recommendation

The `beforeSwap` hook must gate the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Router-forwarded identity in `extensionData`**: Standardize a convention where the router prepends the original `msg.sender` to `extensionData`, and `SwapAllowlistExtension` decodes and checks that address. Because the router is a trusted periphery contract, this forwarded identity can be relied upon.

2. **Extension interface change**: Add an explicit `originalSender` field to `beforeSwap` that the pool populates from a trusted source (e.g., a transient-storage slot set by the router before calling the pool), analogous to how `owner` is explicitly forwarded on the liquidity path.

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists userA:
       setAllowedToSwap(pool, userA, true)
3. Pool admin allowlists the router so userA can use it:
       setAllowedToSwap(pool, router, true)
4. Non-allowlisted userB calls:
       router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(recipient, ...)
   → pool sets sender = msg.sender = router
   → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
   → swap executes — userB bypasses the allowlist entirely.
5. userA's individual allowlist entry (step 2) is irrelevant for router paths;
   the router entry (step 3) is the only one that matters, and it admits everyone.
```

A Foundry integration test can confirm this by: deploying a pool with `SwapAllowlistExtension`, allowlisting only `userA` and the router, then calling `router.exactInputSingle` from `userB` and asserting the swap succeeds rather than reverting with `NotAllowedToSwap`.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
