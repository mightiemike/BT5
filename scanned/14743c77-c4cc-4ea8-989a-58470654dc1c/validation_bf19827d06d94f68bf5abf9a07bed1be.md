### Title
SwapAllowlistExtension Gates the Router Address Instead of the Ultimate User, Allowing Any Unprivileged Caller to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the ultimate user. If the pool admin allowlists the router (a natural operational step so that allowlisted users can use the router), every unprivileged user can bypass the individual-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` with itself as `msg.sender`: [4](#0-3) 

So the extension sees `sender = router`, not the actual user. The allowlist check becomes `allowedSwapper[pool][router]`. If the admin allowlists the router (so that individually-allowlisted users can reach the pool through the router), the gate is wide open: **any** caller of the router passes the check, regardless of whether they are individually allowlisted.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`: [5](#0-4) 

The `addLiquidity` interface explicitly documents the operator pattern (`msg.sender` pays but need not equal `owner`), so the deposit allowlist correctly gates the economically relevant party. The swap allowlist has no equivalent mechanism.

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., a private institutional pool) and allowlists the router so that approved users can use the standard periphery inadvertently opens the pool to every user of the router. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` against the restricted pool and execute swaps that the allowlist was intended to block. Because the pool uses oracle-anchored pricing, a stale or slightly off oracle quote at the moment of the unauthorized swap can be exploited to extract value from LPs. Even without oracle staleness, the access-control invariant is broken: the pool admin's intent to restrict the swap counterparty set is fully defeated by a single router allowlist entry.

---

### Likelihood Explanation

The trigger is unprivileged and requires no special setup beyond the pool admin having allowlisted the router — a routine operational step that any admin who wants their allowlisted users to use the standard periphery would take. The attacker needs only to call the public router with the target pool address. No flash loan, no price manipulation, and no privileged role is required.

---

### Recommendation

The extension must gate the **ultimate user**, not the direct caller of `pool.swap()`. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData` before forwarding to the pool. `SwapAllowlistExtension.beforeSwap` reads and verifies this value. This requires the extension to trust the pool's forwarding of `extensionData`, which is already the established pattern.

2. **Check `sender` and fall back to a router-reported identity**: If `sender` is a known router, the extension reads the actual user from a standardized field in `extensionData` and checks that address against the allowlist instead.

Either approach ensures the allowlist gates the economically relevant actor regardless of whether the swap arrives directly or through the router.

---

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E.
  - Admin allowlists router R: E.setAllowedToSwap(P, router, true).
  - Admin does NOT allowlist attacker A: allowedSwapper[P][A] == false.

Attack:
  1. Attacker A calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...}).
  2. Router calls P.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
     with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[P][router] == true → passes.
  5. Swap executes. Attacker A has successfully swapped against a pool
     that was supposed to block them.

Result:
  allowedSwapper[P][A] == false, yet A's swap completes.
  The allowlist invariant is broken.
```

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
