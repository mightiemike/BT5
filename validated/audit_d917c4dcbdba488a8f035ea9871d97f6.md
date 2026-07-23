Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is set to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the originating user. If the pool admin allowlists the router to enable router-mediated swaps, every unpermissioned address can bypass the per-user allowlist by calling any of the router's entry points.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses that `sender` argument — together with `msg.sender` (the pool) — to look up the allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making `msg.sender` inside the pool the **router address**: [4](#0-3) 

The allowlist check therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originating_user]`. If the pool admin has added the router to the allowlist — the only way to permit router-mediated swaps for any user — the gate passes for **every** caller regardless of their individual allowlist status. The same substitution occurs for `exactInput` (all hops): [5](#0-4) 

And for `exactOutputSingle` and all recursive hops inside `_exactOutputIterateCallback`: [6](#0-5) 

There is no mechanism in the router to encode the originating user into `extensionData`, and no mechanism in the extension to decode and verify such an attestation. The `extensionData` passed to `pool.swap` in `exactInputSingle` is simply `params.extensionData` supplied by the caller — an attacker-controlled value — so it cannot be trusted as an identity attestation. [4](#0-3) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) is fully bypassed by any address routing through `MetricOmmSimpleRouter`. The LP principal in the pool is exposed to arbitrary swappers, defeating the entire purpose of the allowlist. Because the pool's oracle-anchored pricing is designed for a trusted counterparty set, opening it to arbitrary traders causes adverse selection losses for LPs — a direct loss of LP-owned assets above contest thresholds. This is a High severity finding: it results in direct loss of LP funds through adverse selection on a pool whose access control is silently nullified. [7](#0-6) 

## Likelihood Explanation
The scenario requires the pool admin to have added the router to `allowedSwapper[pool][router]`. This is the natural and expected operational step: without it, even individually allowlisted users cannot swap through the supported periphery path. Any production pool that intends to allow router-mediated swaps for its permitted users will have taken this step, making the bypass reachable on every such pool. The attacker needs no special privilege — a single call to `exactInputSingle` suffices, with no front-running, no flash loan, and no privileged access required. [8](#0-7) 

## Recommendation
The extension must resolve the **originating user**, not the direct caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated, authenticated convention between the router and the extension (the extension must verify `msg.sender` is a trusted router before trusting the payload).

2. **Router registry in the extension**: The extension maintains a registry of trusted routers. When `sender` is a known router, it requires the real user to be attested in `extensionData`; otherwise it checks `sender` directly.

The simplest safe interim fix is to remove the router from the per-pool allowlist and require direct pool calls for allowlisted users, documenting that router-mediated swaps are incompatible with per-user `SwapAllowlistExtension` enforcement until the extension is updated. [3](#0-2) 

## Proof of Concept
```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension configured as a beforeSwap extension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is permitted
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router added to enable periphery swaps

Attack
──────
4. charlie (not in allowlist) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      <curated pool>,
           recipient: charlie,
           ...
       })

5. Router calls pool.swap(charlie, ...) with msg.sender = router inside pool.

6. Pool calls _beforeSwap(sender=router, ...).

7. SwapAllowlistExtension checks:
       allowedSwapper[pool][router]  →  true   ✓ (step 3)

8. Swap executes. charlie receives output tokens.
   The check allowedSwapper[pool][charlie] is never evaluated.
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a mock pool with it, allowlist only `alice` and the router, then call `exactInputSingle` from `charlie` and assert the swap succeeds (demonstrating the bypass) and that `allowedSwapper[pool][charlie]` is `false`. [9](#0-8)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

**File:** metric-periphery/test/extensions/SwapAllowlistSubExtension.t.sol (L26-38)
```text
  function test_revertsWhenSwapperNotAllowed() public {
    vm.prank(address(pool));
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }

  function test_passesWhenSwapperAllowed() public {
    vm.prank(admin);
    extension.setAllowedToSwap(address(pool), swapper, true);

    vm.prank(address(pool));
    extension.beforeSwap(swapper, address(0), false, 0, 0, 0, 0, 0, "");
  }
```
