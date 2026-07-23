### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the originating user. If the pool admin allowlists the router (the only way to let any user swap via the router), every user — including those the admin intended to block — can bypass the allowlist by routing through the public router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`** [1](#0-0) 

The extension receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct key for the per-pool mapping). `sender` is whatever the pool passed as the first argument to `beforeSwap`.

**What the pool passes as `sender`** [2](#0-1) 

The pool calls `_beforeSwap(msg.sender, recipient, ...)`, so `sender` = `msg.sender` of the pool's `swap` function.

**What the router passes as `msg.sender` to the pool** [3](#0-2) 

`exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) all call `pool.swap(...)` directly. The pool therefore sees `msg.sender = address(router)`, not the originating EOA.

**The invariant break**

The extension is designed to gate individual swappers per pool. But when a user routes through `MetricOmmSimpleRouter`, the extension sees only the router's address. This creates an irresolvable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot swap via the router at all |
| **Allowlist the router** | Every user — including those explicitly blocked — can bypass the allowlist by calling the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

**Contrast with `DepositAllowlistExtension`** [4](#0-3) 

The deposit extension checks `owner` (the position owner, explicitly passed by the caller), not `sender`. The liquidity adder always forwards the intended `positionOwner` as `owner`, so the deposit allowlist correctly gates the economic beneficiary. The swap allowlist has no equivalent forwarding mechanism — the router cannot inject the originating user's address into the `sender` slot because that slot is set by the pool from `msg.sender`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassable by any unprivileged user who calls `MetricOmmSimpleRouter`. The attacker pays only gas. The pool receives trades from actors the admin explicitly intended to block, breaking the curation invariant and potentially exposing LP funds to unauthorized counterparties or regulatory risk. This matches the contest-relevant impact: **broken core pool functionality causing loss of funds or unusable swap flows**, and **admin-boundary break where an unprivileged path bypasses a configured guard**.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint documented in the periphery. Any user who reads the interface will naturally use the router. No special knowledge or privileged access is required — the bypass is automatic for every router-mediated swap on a pool that has allowlisted the router.

---

### Recommendation

The extension must gate the **originating user**, not the immediate caller. Two sound approaches:

1. **Pass the originating payer through `extensionData`**: The router encodes `msg.sender` (the originating user) into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and checks that address instead of `sender`. This requires a convention between the router and the extension.

2. **Check `sender` only when `sender` is not a known router**: The extension maintains a registry of trusted routers; when `sender` is a router, it decodes the real user from `extensionData`. When `sender` is a direct EOA, it checks `sender` directly.

Either way, the extension must be updated so that the checked identity is always the economically relevant actor, regardless of which supported periphery entrypoint reaches the pool.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)
    (necessary so that allowlisted users can use the router)
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - Attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for the attacker despite them not being on the allowlist

Result:
  - Attacker successfully trades in a curated pool
  - Every non-allowlisted user can repeat this via the public router
  - The pool admin's allowlist is completely ineffective for router-mediated swaps
``` [1](#0-0) [2](#0-1) [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
