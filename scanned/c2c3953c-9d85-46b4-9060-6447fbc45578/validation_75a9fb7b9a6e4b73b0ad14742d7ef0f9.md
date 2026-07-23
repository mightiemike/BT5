### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end user. This creates an irresolvable dilemma for pool admins: either (a) allowlist the router and let any user bypass the restriction, or (b) do not allowlist the router and break router-mediated swaps for all allowed users.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is the `msg.sender` of the pool's `swap` call:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← original caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point), the call chain is:

```
User → Router.exactInputSingle → pool.swap(msg.sender = Router) → extension.beforeSwap(sender = Router)
```

The extension therefore checks `allowedSwapper[pool][Router]`, not `allowedSwapper[pool][User]`.

**Scenario A — Router is allowlisted (bypass):**
The pool admin allowlists the router so that permitted users can trade via the router. Any unpermitted user can now call `router.exactInputSingle(pool, ...)` and the extension passes because `allowedSwapper[pool][router] == true`. The allowlist is fully bypassed.

**Scenario B — Router is not allowlisted (broken functionality):**
Permitted users cannot use the router at all; they must call the pool directly. This breaks the core swap flow for the supported periphery path.

The `DepositAllowlistExtension` does **not** share this flaw — it correctly checks `owner` (the economic beneficiary of the LP position), which the `MetricOmmPoolLiquidityAdder` passes explicitly and which is independent of the router/adder address.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional market makers, KYC'd users, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against LP liquidity that was deposited under the assumption of a curated, restricted trading environment. This constitutes a direct admin-boundary break with fund-impacting consequences for LPs: they bear swap risk from counterparties the pool was explicitly designed to exclude.

---

### Likelihood Explanation

The likelihood is high. Any pool admin who wants to allow permitted users to trade via the router must allowlist the router — this is the natural and expected configuration. Once the router is allowlisted, the bypass is trivially reachable by any public user with no special privileges, no preconditions, and no multi-step setup.

---

### Recommendation

The extension must gate the **end user**, not the immediate pool caller. Two viable approaches:

1. **Pass the end user through `extensionData`:** The router encodes `msg.sender` (the end user) into `extensionData`; the extension decodes and checks that address. This requires a trusted encoding convention.

2. **Check `sender` only when `sender` is not a known router:** Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`.

The simplest correct fix is to have the router always encode the originating user in `extensionData` and have the extension decode and check that identity when `sender` is a recognized router address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is permitted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - charlie (not permitted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(msg.sender = router, ...)
  - Pool calls extension.beforeSwap(sender = router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - charlie's swap executes on the restricted pool

Result:
  - charlie trades against LP liquidity on a pool that was supposed to exclude him
  - The allowlist provides zero protection for router-mediated swaps
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
