Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Economic Actor, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on `sender`, which the pool sets to `msg.sender` of the `swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router to enable router-based swaps for curated users, every unprivileged caller of the router passes the allowlist check, completely defeating the pool's curation policy.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to all configured extensions: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. If the pool admin allowlists the router address to enable router-based swaps for curated users, the check passes for every caller of the router, not just the intended ones.

This is a design inconsistency confirmed by contrast with `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates on `owner` (the LP position owner — the economic actor) rather than `sender` (the intermediary caller): [5](#0-4) 

The swap extension has no equivalent distinction between the intermediary caller and the economic actor.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses is fully bypassed. Any unprivileged user who calls through `MetricOmmSimpleRouter` trades on the curated pool as if they were allowlisted. This is a direct policy failure: the pool's curation invariant — that only approved addresses may swap — is silently violated on every router-mediated trade. The wrong value is the extension decision: `allowedSwapper[pool][router]` evaluates `true` when it should evaluate `allowedSwapper[pool][attacker]` which is `false`.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router address. This is a foreseeable operational step: allowlisted users who want multi-hop swaps or slippage protection through the router cannot use it unless the router itself is allowlisted, because the extension will see `sender = router` and reject them. The admin is therefore pushed toward allowlisting the router to make the pool usable for legitimate users, which simultaneously opens the gate to all users. The precondition is semi-trusted but operationally foreseeable and not malicious in intent.

## Recommendation
Gate on the economic actor, not the intermediary caller. The cleanest fix mirrors `DepositAllowlistExtension`: identify a distinct parameter representing the actual swapper (e.g., the `recipient`, or a transient-storage slot set by the router before calling the pool, analogous to how the router already stores payer context via `_setNextCallbackContext`). Alternatively, document and enforce at the extension level that the router address must never be allowlisted, reverting if `sender` is a known router contract.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` — pool sets `sender = address(router)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → passes.
6. Attacker's swap executes on the curated pool despite never being allowlisted.

The check at line 37 evaluates `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][attacker]` (false), so the `NotAllowedToSwap` revert never fires. [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
